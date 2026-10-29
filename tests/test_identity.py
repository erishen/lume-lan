"""身份可信层的单测：冒充必须失败。

背景: 原协议所有节点共用一个 PEER_SHARED，签名里虽带 node，但任何持钥者
都能用别人的 node 名去签。于是「领域映射里谁审」只是排班意图，审计里的
actor 也只是自称。

本层把密钥按节点派生（root + node → key），服务端按**声称的 node** 取同一
派生密钥验签 —— 拿自己的密钥冒充别人，签名必然对不上。

这些用例是**授权语义**的护栏：它们失败意味着「谁都能声称自己是 human_admin」。
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LAN_ROOT = os.path.dirname(HERE)
WORK = os.path.dirname(LAN_ROOT)
CORE = os.path.join(WORK, "lume-core")
LUME_CORE = os.path.join(CORE, "bin", "lume-core")

HAS_CORE = os.path.exists(LUME_CORE)

# 注意: import 按**脚本所在目录**解析, 脚本放临时目录时必须用绝对路径
# （这一点在 test_domains 里也踩过一次，那里是靠 cwd 对齐才生效的）
LIB = os.path.join(CORE, "lib")

DERIVE = r'''
import "%s/identity.lume" as ident;
print("ENABLED=" + str(ident.enabled()));
print("KNOWN_SCANNER=" + str(ident.known("scanner")));
print("KNOWN_GOD=" + str(ident.known("god")));
print("K_SCANNER=" + ident.key_for("scanner"));
print("K_BLOG=" + ident.key_for("blog"));
''' % LIB

REGISTRY = '{"peers":["scanner","blog","crm"]}'


@unittest.skipUnless(HAS_CORE, "lume-core 二进制不存在，跳过")
class TestIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # 把 lib 链到真实目录（只读用），registry 指向临时文件
        self.lib_src = os.path.join(CORE, "lib")
        self.reg = os.path.join(self.tmp.name, "peers-identity.json")
        with open(self.reg, "w", encoding="utf-8") as f:
            f.write(REGISTRY)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, script: str, root: str, name: str = "scanner") -> dict:
        path = os.path.join(self.tmp.name, "s.lume")
        with open(path, "w", encoding="utf-8") as f:
            f.write(script)
        env = dict(os.environ)
        env["PEER_ROOT"] = root
        env["PEER_NAME"] = name
        env["PEER_REGISTRY"] = self.reg
        # cwd 必须是 lume-core，才能 import "lib/..."
        r = subprocess.run([LUME_CORE, path], capture_output=True, text=True,
                           timeout=60, cwd=CORE, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = {}
        for line in r.stdout.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
        return out

    def test_enabled_only_with_root(self):
        got = self._run(DERIVE, root="some-root")
        self.assertEqual(got["ENABLED"], "true")

    def test_known_uses_registry(self):
        got = self._run(DERIVE, root="r")
        self.assertEqual(got["KNOWN_SCANNER"], "true")
        self.assertEqual(got["KNOWN_GOD"], "false")

    def test_keys_are_per_node(self):
        got = self._run(DERIVE, root="r")
        self.assertNotEqual(got["K_SCANNER"], got["K_BLOG"],
                            "不同节点的派生密钥必须不同，否则仍可冒充")

    def test_key_depends_on_root(self):
        a = self._run(DERIVE, root="root-A")
        b = self._run(DERIVE, root="root-B")
        self.assertNotEqual(a["K_SCANNER"], b["K_SCANNER"],
                            "换 root 必须换出不同密钥，否则 root 轮换无效")

    def test_key_is_stable_for_same_root(self):
        a = self._run(DERIVE, root="same")
        b = self._run(DERIVE, root="same")
        self.assertEqual(a["K_SCANNER"], b["K_SCANNER"],
                         "同一 root 下必须稳定，否则客户端/服务端对不上")

    def test_python_mirror_matches_lume(self):
        # Python 侧（worker）要独立算同一个派生密钥，规则必须一致
        import hashlib
        import hmac
        got = self._run(DERIVE, root="mirror-root")
        expected = hmac.new(b"mirror-root", b"peer:scanner", hashlib.sha256).hexdigest()
        self.assertEqual(got["K_SCANNER"], expected,
                         "python 的派生规则与 lume 不一致")


if __name__ == "__main__":
    unittest.main()
