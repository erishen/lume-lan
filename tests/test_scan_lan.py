"""scan_lan.py 的单测: 平台感知的 ping 参数 + detect_host_ip 的物理接口优先。

只测纯逻辑(参数构造 / 回退顺序), 不真发包、不依赖本机网络。
跑法: make test   (或 python3 -m unittest discover -s tests)
"""
import os
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
