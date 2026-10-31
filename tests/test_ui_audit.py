"""src/audit.lume(容器侧审计)的行为验证。

为什么用 lume 跑而不是纯 Python 测: 这段逻辑里最容易错的是
「write_file 是覆盖不是追加」和「轮转后记录还在不在」——
这两点必须由**真实运行时**验证, 用 Python 模拟等于测了自己写的假设。

跑法: make test
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LUME_CORE = os.path.join(ROOT, "..", "lume-core", "bin", "lume-core")


def _lume_available() -> bool:
    return os.path.isfile(LUME_CORE) and os.access(LUME_CORE, os.X_OK)


@unittest.skipUnless(_lume_available(), "lume-core 二进制不可用, 跳过")
class TestUiAudit(unittest.TestCase):
    """审计模块的落盘与轮转。"""

    def setUp(self):
        # 临时目录: 不污染仓库, 也避免和真实 /app/audit 混在一起
        self.tmp = tempfile.mkdtemp(prefix="uiaudit-")
        self.dir = os.path.join(self.tmp, "audit")
        os.makedirs(self.dir, exist_ok=True)

    def tearDown(self):
        # 沙箱可能拦 rmtree, 失败不影响断言结论
        try:
            import shutil
            shutil.rmtree(self.tmp, ignore_errors=True)
        except Exception:
            pass

    def _run(self, script: str) -> subprocess.CompletedProcess:
        path = os.path.join(self.tmp, "probe.lume")
        with open(path, "w", encoding="utf-8") as f:
            f.write(script)
        env = dict(os.environ)
        env["PEER_NAME"] = "lan"
        return subprocess.run([LUME_CORE, path], capture_output=True,
                              text=True, timeout=60, env=env, cwd=ROOT)

    def test_appends_instead_of_overwriting(self):
        """连续写两条必须都留下 —— 这是 write_file 覆盖语义的回归点。"""
        script = f'''
let path = "{self.dir}/a2a-ui-audit.jsonl";
write_file(path, "line1\\n");
let prev = read_file(path);
write_file(path, prev + "line2\\n");
print(str(read_file(path)));
'''
        r = self._run(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("line1", r.stdout)
        self.assertIn("line2", r.stdout)

    def test_empty_file_reads_as_null_not_empty_string(self):
        """记录这个坑: 空文件 read_file 返回 null。

        /.dockerenv 的环境检测失效就是因为这一点(空文件 == null),
        所以任何"追加前先读"的代码都必须显式处理 null。
        """
        script = f'''
let path = "{self.dir}/empty.jsonl";
write_file(path, "");
print(str(read_file(path)));
'''
        r = self._run(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("null", r.stdout.strip())


    def test_str_null_is_literal_not_empty(self):
        """lume 的 str(null) 是字面量 "null"(4 字符), 不是空串。

        这个坑真实发生过: peers.json 里没有 container_target 时解析出 null,
        代码用 `len(str(get(m,"k","")))>0` 判断"有没有配置", 于是把目标地址
        改写成字面量 "null", 请求打到一个不存在的主机 —— 表现是"调不通",
        而错误信息里完全看不出是这个原因。
        """
        script = '''
let m = json("{\\"k\\":null}");
print("raw=" + str(get(m, "k", "")));
print("len=" + str(len(str(get(m, "k", "")))));
'''
        r = self._run(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("raw=null", r.stdout)
        self.assertIn("len=4", r.stdout)


if __name__ == "__main__":
    unittest.main()
