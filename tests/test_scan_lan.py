"""scan_lan.py 的单测: ping 参数 / host IP 回退 / 真实掩码同网段校验 / sweep 合并与噪音过滤。

只测纯逻辑(参数构造 / 回退顺序), 不真发包、不依赖本机网络。
跑法: make test   (或 python3 -m unittest discover -s tests)
"""
import os
import sys
import unittest
from ipaddress import IPv4Network
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import scan_lan as S  # noqa: E402


def _run_stdout(stdout: str):
    """构造一个替代 subprocess.run 的桩, 固定返回 stdout。"""
    def fake(cmd, **kw):
        return mock.Mock(stdout=stdout)
    return fake


class TestPingArgs(unittest.TestCase):
    def test_macos_uses_seconds_timeout_and_ms_wait(self):
        with mock.patch.object(S.sys, "platform", "darwin"):
            self.assertEqual(S._ping_args(), ["-c", "1", "-t", "1", "-W", "1000"])

    def test_linux_uses_seconds_wait_without_ttl_flag(self):
        with mock.patch.object(S.sys, "platform", "linux"):
            self.assertEqual(S._ping_args(), ["-c", "1", "-W", "1"])

    def test_ping_one_builds_cmd_and_reads_returncode(self):
        seen = {}

        def fake(cmd, **kw):
            seen["cmd"] = cmd
            return mock.Mock(returncode=0)

        with mock.patch.object(S.subprocess, "run", fake):
            out = S.ping_one("192.168.1.5")
        self.assertTrue(out["live"])
        self.assertEqual(seen["cmd"][0], "ping")
        self.assertEqual(seen["cmd"][-1], "192.168.1.5")

    def test_ping_one_offline(self):
        with mock.patch.object(S.subprocess, "run", lambda *a, **k: mock.Mock(returncode=2)):
            self.assertFalse(S.ping_one("192.168.1.250")["live"])


class TestHostIp(unittest.TestCase):
    def test_prefers_physical_default_route_iface(self):
        ifaces = {"en0": "192.168.3.57", "utun3": "10.8.0.2"}
        route = "   interface: en0\n   gateway: 192.168.3.1\n"
        with mock.patch.object(S, "_host_ipv4s", lambda: ifaces), \
             mock.patch.object(S.subprocess, "run", _run_stdout(route)):
            self.assertEqual(S.detect_host_ip(), "192.168.3.57")

    def test_vpn_default_route_falls_back_to_gateway_subnet(self):
        # 默认出口是 VPN(utun) → 跳过, 退回与本机可信网关同网段的物理接口
        ifaces = {"utun3": "10.8.0.2", "en0": "192.168.3.57"}
        route = "   interface: utun3\n"
        with mock.patch.object(S, "_host_ipv4s", lambda: ifaces), \
             mock.patch.object(S, "detect_gateway", lambda: "192.168.3.1"), \
             mock.patch.object(S.subprocess, "run", _run_stdout(route)):
            self.assertEqual(S.detect_host_ip(), "192.168.3.57")

    def test_loopback_when_nothing_available(self):
        with mock.patch.object(S, "_host_ipv4s", lambda: {}), \
             mock.patch.object(S, "detect_gateway", lambda: ""), \
             mock.patch.object(S.subprocess, "run", _run_stdout("")), \
             mock.patch("socket.socket") as sk:
            sk.return_value.getsockname.return_value = ("127.0.0.1", 0)
            self.assertEqual(S.detect_host_ip(), "127.0.0.1")


class TestNetmask(unittest.TestCase):
    def test_hex_and_dotted(self):
        self.assertEqual(S._netmask_to_prefix("0xffffff00"), 24)
        self.assertEqual(S._netmask_to_prefix("0xffffffc0"), 26)
        self.assertEqual(S._netmask_to_prefix("255.255.255.0"), 24)
        self.assertEqual(S._netmask_to_prefix("255.255.0.0"), 16)

    def test_invalid_returns_none(self):
        self.assertIsNone(S._netmask_to_prefix("bogus"))
        self.assertIsNone(S._netmask_to_prefix("255.0.255.0"))  # 非连续掩码
        self.assertIsNone(S._netmask_to_prefix(""))


class TestArpParsing(unittest.TestCase):
    ARP = (
        "? (192.168.3.1) at c4:6:83:71:74:4 on en0 ifscope [ethernet]\n"
        "? (192.168.3.36) at 84:9d:c2:72:7a:69 on en0 ifscope [ethernet]\n"
        "? (192.168.3.2) at (incomplete) on en0 ifscope [ethernet]\n"
    )

    def test_snapshot_parses_non_zero_padded_mac(self):
        # 回归: 旧正则要求每段恰好 2 位十六进制, macOS 的 c4:6:… 会被漏成空串,
        # 进而被 is_noise「空 MAC 即噪音」丢弃。
        with mock.patch.object(S.subprocess, "run", _run_stdout(self.ARP)):
            dev = {d["ip"]: d for d in S.arp_snapshot()}
        self.assertEqual(dev["192.168.3.1"]["mac"], "c4:06:83:71:74:04")
        self.assertEqual(dev["192.168.3.36"]["mac"], "84:9d:c2:72:7a:69")
        self.assertEqual(dev["192.168.3.2"]["mac"], "")
        self.assertFalse(dev["192.168.3.2"]["live"])

    def test_gateway_from_arp_matches_non_padded_mac(self):
        arp = "? (192.168.3.1) at c4:6:83:71:74:4 on en0 ifscope [ethernet]\n"
        with mock.patch.object(S.subprocess, "run", _run_stdout(arp)):
            self.assertEqual(S._gateway_from_arp({"en0": "192.168.3.57"}), "192.168.3.1")


class TestHostIfaceNetworks(unittest.TestCase):
    MACOS = (
        "en0: flags=8863<UP,BROADCAST,RUNNING,MULTICAST> mtu 1500\n"
        "\tinet 192.168.3.57 netmask 0xffffff00 broadcast 192.168.3.255\n"
        "utun3: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1380\n"
        "\tinet 10.8.0.2 --> 10.8.0.2 netmask 0xffffffff\n"
    )
    LINUX = (
        "eth0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500\n"
        "        inet 192.168.1.20  netmask 255.255.255.0  broadcast 192.168.1.255\n"
        "docker0: flags=4099<UP,BROADCAST,MULTICAST>  mtu 1500\n"
        "        inet 172.17.0.1/16  brd 172.17.255.255  scope global docker0\n"
    )

    def test_parses_macos_hex_netmask(self):
        with mock.patch.object(S.subprocess, "run", _run_stdout(self.MACOS)):
            nets = S._host_iface_networks()
        self.assertEqual(str(nets["en0"]), "192.168.3.0/24")
        self.assertEqual(str(nets["utun3"]), "10.8.0.2/32")

    def test_parses_linux_dotted_and_cidr(self):
        with mock.patch.object(S.subprocess, "run", _run_stdout(self.LINUX)):
            nets = S._host_iface_networks()
        self.assertEqual(str(nets["eth0"]), "192.168.1.0/24")
        self.assertEqual(str(nets["docker0"]), "172.17.0.0/16")


class TestSameLan(unittest.TestCase):
    HOSTS = {"en0": "192.168.3.57"}

    def _nets(self):
        return mock.patch.object(S, "_host_iface_networks",
                                 lambda: {"en0": IPv4Network("192.168.3.0/24")})

    def test_accepts_same_subnet_by_real_mask(self):
        with self._nets():
            self.assertTrue(S._same_lan("192.168.3.1", self.HOSTS))

    def test_rejects_adjacent_subnet_that_loose_16_would_accept(self):
        # 真实掩码 /24: 192.168.5.1 与 192.168.3.57 不同子网 → 拒绝(旧固定 /16 会误判为同网)
        with self._nets():
            self.assertFalse(S._same_lan("192.168.5.1", self.HOSTS))

    def test_rejects_vpn_gateway(self):
        with self._nets():
            self.assertFalse(S._same_lan("74.125.250.129", self.HOSTS))

    def test_explicit_prefix_stays_loose_backcompat(self):
        self.assertTrue(S._same_lan("192.168.5.1", self.HOSTS, prefix=16))


class TestIsNoise(unittest.TestCase):
    def test_multicast_reserved_is_noise(self):
        for ip in ("239.255.255.250", "224.0.0.251"):
            self.assertTrue(S.is_noise(ip, "01:00:5e:7f:ff:fa", "en0"))

    def test_real_device_is_kept(self):
        self.assertFalse(S.is_noise("192.168.3.36", "84:9d:c2:72:7a:69", "en0"))

    def test_empty_mac_is_noise(self):
        self.assertTrue(S.is_noise("192.168.3.99", "", ""))


class TestSweepMergeAndNoise(unittest.TestCase):
    def test_sweep_only_live_device_survives_noise_filter(self):
        # 回归: sweep 命中设备只有 IP、无 MAC, is_noise 会判为噪音; 必须保留否则 LAN_SWEEP 失效
        devices = [
            {"ip": "192.168.3.1", "mac": "aa:aa:aa:aa:aa:aa", "iface": "en0",
             "live": True, "source": "arp", "gateway": True},
            {"ip": "192.168.3.74", "mac": "", "iface": "", "live": True, "source": "sweep"},
        ]
        self.assertEqual([d["ip"] for d in S._filter_noise(devices)],
                         ["192.168.3.1", "192.168.3.74"])

    def test_incomplete_arp_without_mac_is_still_noise(self):
        devices = [{"ip": "192.168.3.99", "mac": "", "iface": "", "live": False, "source": "arp"}]
        self.assertEqual(S._filter_noise(devices), [])

    def test_merge_adds_new_sweep_host_and_flags_arp_live(self):
        devices = [{"ip": "192.168.3.1", "mac": "aa:aa:aa:aa:aa:aa",
                    "iface": "en0", "live": False, "source": "arp"}]
        swn = [{"ip": "192.168.3.1", "live": True, "source": "sweep"},
               {"ip": "192.168.3.74", "live": True, "source": "sweep"}]
        out = S._merge_devices(devices, swn)
        self.assertEqual(sorted(d["ip"] for d in out), ["192.168.3.1", "192.168.3.74"])
        self.assertTrue(next(d for d in out if d["ip"] == "192.168.3.1")["live"])


if __name__ == "__main__":
    unittest.main()
