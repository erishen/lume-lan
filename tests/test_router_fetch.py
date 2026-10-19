"""router_fetch.py 流量历史的单测: 身份键(MAC) / 迁移 / 增量 / IP 复用隔离。

对着真实 .data/*.json 路径跑会污染宿主数据, 所以每个用例把模块级路径指到临时目录。
跑法: make test   (或 python3 -m unittest discover -s tests)
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import router_fetch as R  # noqa: E402

MB = 1024 * 1024


def dev(ip, mac, rx=0, tx=0, online=True):
    return {"ip": ip, "mac": mac, "rx_bytes": rx, "tx_bytes": tx, "online": online}


class HistCase(unittest.TestCase):
    """把 HIST / PAYLOAD / HIST_TOTAL 重定向到临时目录。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (R.HIST, R.PAYLOAD, R.HIST_TOTAL)
        R.HIST = os.path.join(self.tmp.name, "hist.json")
        R.PAYLOAD = os.path.join(self.tmp.name, "payload.json")
        R.HIST_TOTAL = os.path.join(self.tmp.name, "total.json")

    def tearDown(self):
        R.HIST, R.PAYLOAD, R.HIST_TOTAL = self._orig
        self.tmp.cleanup()

    def write_hist(self, obj):
        with open(R.HIST, "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def read_hist(self):
        with open(R.HIST, encoding="utf-8") as f:
            return json.load(f)

    def read_payload(self):
        with open(R.PAYLOAD, encoding="utf-8") as f:
            return json.load(f)


class TestDevKey(unittest.TestCase):
    def test_mac_preferred_and_lowercased(self):
        self.assertEqual(R._dev_key(dev("192.168.1.5", "AA:BB:CC:DD:EE:FF")),
                         "mac:aa:bb:cc:dd:ee:ff")

    def test_ip_fallback_when_no_mac(self):
        self.assertEqual(R._dev_key(dev("192.168.1.5", "")), "ip:192.168.1.5")


class TestMigrateHist(HistCase):
    def test_old_ip_keys_rekeyed_to_mac_and_get_ip_backfilled(self):
        old = {"192.168.1.5": [{"ts": 1000, "rx": 1, "tx": 2}]}
        out = R._migrate_hist(old, [dev("192.168.1.5", "aa:aa:aa:aa:aa:aa")])
        self.assertIn("mac:aa:aa:aa:aa:aa:aa", out)
        self.assertEqual(out["mac:aa:aa:aa:aa:aa:aa"][0]["ip"], "192.168.1.5")

    def test_unknown_old_key_falls_back_to_ip_prefixed(self):
        out = R._migrate_hist({"192.168.1.9": [{"ts": 1, "rx": 0, "tx": 0}]}, [])
        self.assertIn("ip:192.168.1.9", out)

    def test_new_format_keys_are_left_alone(self):
        cur = {"mac:aa:aa:aa:aa:aa:aa": [{"ts": 1, "rx": 5, "tx": 0, "ip": "192.168.1.5"}]}
        self.assertEqual(R._migrate_hist(cur, []), cur)


class TestTrafficDeltas(HistCase):
    def test_delta_1h_over_baseline(self):
        # baseline 700s ago (> 600s) → delta_1h = 现累计 - 基线
        self.write_hist({"192.168.1.5": [{"ts": 1000, "rx": 0, "tx": 0}]})
        d = [dev("192.168.1.5", "aa:aa:aa:aa:aa:aa", rx=5 * MB)]
        R.apply_traffic_deltas(d, 1700)
        self.assertEqual(d[0]["delta_1h"], 5 * MB)

    def test_delta_1h_none_when_history_too_short(self):
        d = [dev("192.168.1.5", "aa:aa:aa:aa:aa:aa", rx=5 * MB)]
        R.apply_traffic_deltas(d, 1700)
        self.assertIsNone(d[0]["delta_1h"])
        self.assertEqual(d[0]["delta_cycle"], 0)

    def test_counter_reset_clamped_to_zero(self):
        # 上一周期 500MB, 本周期 100MB(设备重启计数器归零) → 周期增量夹到 0
        self.write_hist({"mac:aa:aa:aa:aa:aa:aa":
                         [{"ts": 1000, "rx": 500 * MB, "tx": 0, "ip": "192.168.1.5"}]})
        d = [dev("192.168.1.5", "aa:aa:aa:aa:aa:aa", rx=100 * MB)]
        R.apply_traffic_deltas(d, 1060)
        self.assertEqual(d[0]["delta_cycle"], 0)

    def test_ip_reuse_keeps_series_separate(self):
        self.write_hist({"192.168.1.5": [{"ts": 1000, "rx": 0, "tx": 0}]})
        R.apply_traffic_deltas([dev("192.168.1.5", "aa:aa:aa:aa:aa:aa", rx=0)], 1700)
        # 另一台设备拿到同一 IP: 不能与旧设备曲线混算
        d = [dev("192.168.1.5", "cc:cc:cc:cc:cc:cc", rx=9 * MB)]
        R.apply_traffic_deltas(d, 2400)
        keys = set(self.read_hist().keys())
        self.assertIn("mac:aa:aa:aa:aa:aa:aa", keys)
        self.assertIn("mac:cc:cc:cc:cc:cc:cc", keys)
        self.assertIsNone(d[0]["delta_1h"])   # 新设备只有 1 个点


class TestPayload(HistCase):
    def test_payload_keyed_by_current_owner_on_ip_reuse(self):
        # aa 曾占 .5, 现在 cc 占 .5: payload 的 .5 应指向当前属主 cc
        self.write_hist({
            "mac:aa:aa:aa:aa:aa:aa": [{"ts": 1, "rx": 0, "tx": 0, "ip": "192.168.1.5"}],
            "mac:cc:cc:cc:cc:cc:cc": [{"ts": 2, "rx": 100, "tx": 0, "ip": "192.168.1.5"}],
        })
        R._save_traffic_payload(self.read_hist(), [dev("192.168.1.5", "cc:cc:cc:cc:cc:cc")])
        p = self.read_payload()
        self.assertEqual(list(p["series"].keys()), ["192.168.1.5"])
        self.assertEqual(p["series"]["192.168.1.5"][-1]["rx"], 100)

    def test_offline_history_still_emitted_under_its_ip(self):
        self.write_hist({
            "mac:aa:aa:aa:aa:aa:aa": [{"ts": 1, "rx": 7, "tx": 0, "ip": "192.168.1.7"}],
        })
        R._save_traffic_payload(self.read_hist(), [])
        self.assertEqual(self.read_payload()["series"]["192.168.1.7"][-1]["rx"], 7)


if __name__ == "__main__":
    unittest.main()
