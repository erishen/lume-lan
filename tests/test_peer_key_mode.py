"""签名模式自检的单测。

背景：peer 支持三套凭据（`PEER_SHARED` / `PEER_ROOT` / `PEER_PRIVKEY`），
优先级是 ed25519 > derived > shared。同时配多个时**后者静默胜出** ——
四个节点的实测配置里就有两个都配着，运行的却是 derived 模式，从配置上
完全看不出来。这跟上一轮修的 `PEER_BIND` 是同一类问题：配了不生效，
现象却表现为"密钥配错了"（拿共享密钥签名一路 401）。

`key_mode()` / `config_warnings()` 就是把这个隐含优先级变成可读状态。

这些用例守的是：**优先级改变或新增模式时必须显式更新这里**，否则
运维会依据过时的心智模型排障。
"""
import json
import os
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LAN_ROOT = os.path.dirname(HERE)
WORK = os.path.dirname(LAN_ROOT)
CORE = os.path.join(WORK, "lume-core")
LUME_CORE = os.path.join(CORE, "bin", "lume-core")

HAS_CORE = os.path.exists(LUME_CORE)

PEER_LIB = os.path.join(CORE, "lib", "peer.lume")


def _probe(env_extra: dict):
    """在指定环境变量下求 key_mode() 与 config_warnings()。"""
    script = f'''
import "{PEER_LIB}" as peer;
print(json({{ mode: peer.key_mode(), warnings: peer.config_warnings() }}));
'''
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PEER_")}
    env.update(env_extra)
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "probe.lume")
        with open(path, "w", encoding="utf-8") as f:
            f.write(script)
        bin_ = LUME_CORE if HAS_CORE else os.path.join(WORK, "lume", "bin", "lume")
        p = subprocess.run([bin_, path], capture_output=True, text=True,
                           timeout=120, env=env, cwd=td)
        for line in reversed(p.stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        raise AssertionError(
            f"拿不到结果 rc={p.returncode}\nstdout={p.stdout[-500:]}\nstderr={p.stderr[-500:]}"
        )


@unittest.skipUnless(HAS_CORE, "lume-core 二进制不存在，跳过")
class KeyModeTest(unittest.TestCase):
    def test_no_creds_means_off(self):
        r = _probe({})
        self.assertEqual(r["mode"], "off")
        self.assertEqual(r["warnings"], [])

    def test_shared_only(self):
        r = _probe({"PEER_SHARED": "k" * 20})
        self.assertEqual(r["mode"], "shared")
        self.assertEqual(r["warnings"], [])

    def test_root_wins_over_shared_and_warns(self):
        """两者都配：derived 生效，且必须警告 —— 这是本次修复的核心。

        不加警告时，运维看到 .env 里写着 PEER_SHARED，就会用共享密钥去
        构造签名，然后收到 401，一路去查密钥是否抄错。
        """
        r = _probe({"PEER_SHARED": "k" * 20, "PEER_ROOT": "r" * 16})
        self.assertEqual(r["mode"], "derived")
        self.assertEqual(len(r["warnings"]), 1,
                         f"应恰好 1 条警告，实际: {r['warnings']}")
        self.assertIn("PEER_SHARED", r["warnings"][0])
        self.assertIn("ignored", r["warnings"][0])

    def test_privkey_wins_over_both(self):
        """配了 PEER_PRIVKEY：ed25519 生效，另外两个都要警告。"""
        with tempfile.TemporaryDirectory() as td:
            sk = os.path.join(td, "peer.sk")
            # 64 个 hex 字符即可（不必是"有效"密钥，这里只测模式判定）
            with open(sk, "w", encoding="utf-8") as f:
                f.write("ab" * 32)
            r = _probe({
                "PEER_SHARED": "k" * 20,
                "PEER_ROOT": "r" * 16,
                "PEER_PRIVKEY": sk,
            })
        self.assertEqual(r["mode"], "ed25519")
        joined = " | ".join(r["warnings"])
        self.assertIn("PEER_SHARED", joined)
        self.assertIn("PEER_ROOT", joined)
        self.assertEqual(len(r["warnings"]), 2,
                         f"应 2 条警告，实际: {r['warnings']}")

    def test_invalid_privkey_falls_back_to_derived(self):
        """私钥文件内容不合法（非 64 hex）时不能静默当成 ed25519。

        静默"启用了 ed25519 但密钥读不出来"是最坏的情况：签名会失败，
        而 mode 却显示 ed25519，排查方向从一开始就是错的。
        """
        with tempfile.TemporaryDirectory() as td:
            sk = os.path.join(td, "bad.sk")
            with open(sk, "w", encoding="utf-8") as f:
                f.write("not-a-valid-key\n")
            r = _probe({"PEER_PRIVKEY": sk, "PEER_ROOT": "r" * 16})
        self.assertEqual(r["mode"], "derived",
                         "非法私钥必须退回 derived，而不是报 ed25519")

    def test_missing_privkey_file_is_reported(self):
        """私钥文件读不到时，必须报出来，而不是静默当成"没配"。

        静默的后果：mode=off，peer 面整体按未配置处理；运维看到"对端连不上"，
        而 .env 里明明写着私钥路径 —— 没有任何线索指向"文件没读到"。
        """
        missing = "/nonexistent/definitely-not-here/peer.sk"
        r = _probe({"PEER_PRIVKEY": missing})
        self.assertEqual(r["mode"], "off")
        self.assertEqual(len(r["warnings"]), 1, r["warnings"])
        self.assertIn("PEER_PRIVKEY", r["warnings"][0])

    def test_invalid_privkey_is_reported(self):
        """格式不对同样要报，且报出实际长度便于定位（多打了一个换行等）。"""
        with tempfile.TemporaryDirectory() as td:
            sk = os.path.join(td, "short.sk")
            with open(sk, "w", encoding="utf-8") as f:
                f.write("abc\n")
            r = _probe({"PEER_PRIVKEY": sk})
        self.assertEqual(r["mode"], "off")
        self.assertEqual(len(r["warnings"]), 1, r["warnings"])
        self.assertIn("64 hex", r["warnings"][0])

    def test_warning_does_not_leak_path(self):
        """警告里不能带私钥文件的绝对路径。

        这个字符串会进 /peer/health 响应和对端日志 —— 路径暴露本机用户名
        与目录结构。运维本机看一眼 .env 就知道路径，不需要让远端知道。
        """
        missing = "/nonexistent/secret-dir/peer.sk"
        r = _probe({"PEER_PRIVKEY": missing})
        for w in r["warnings"]:
            self.assertNotIn("/nonexistent", w, f"警告泄露了路径: {w}")
            self.assertNotIn("secret-dir", w, f"警告泄露了路径: {w}")

    def test_error_message_never_contains_key_material(self):
        """私钥内容绝不能出现在警告里 —— 哪怕它是错的。

        错误信息会进日志；把"错的密钥"写进日志，等于把接近正确的密钥材料
        也写进去了（常常只差一个字符）。
        """
        with tempfile.TemporaryDirectory() as td:
            sk = os.path.join(td, "almost.sk")
            secret = "deadbeef" * 8          # 64 个 hex，合法输入
            with open(sk, "w", encoding="utf-8") as f:
                f.write(secret[:-1])          # 63 个：不合法
            r = _probe({"PEER_PRIVKEY": sk})
        joined = " | ".join(r["warnings"])
        self.assertNotIn(secret[:-1], joined, "警告里出现了密钥内容")
        self.assertIn("63", joined, "应报出实际长度")


if __name__ == "__main__":
    unittest.main()
