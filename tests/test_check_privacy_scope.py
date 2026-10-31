"""check_privacy.py 的扫描范围测试。

背景：2026-10 有一批 31 个新增文件从来没被隐私检查看过 —— 旧实现只跑
`git ls-files`（已跟踪文件）。而「新写的文件」恰恰最容易把真实网段写进去
（复制一段实测记录、贴一条 curl 命令）。等到 `git add` 之后 pre-commit
才第一次看到内容，那时发现问题已经晚了。

这些用例守的是**扫描范围**，不是规则本身：
  1. 未跟踪文件必须被扫到（本次修复的核心）
  2. .gitignore 覆盖的文件不该报（否则噪音会淹掉真问题）
  3. --tracked 能退回旧行为（有人需要时）
  4. --staged 只看暂存区（pre-commit 用，语义不能变）

实现说明：用例在临时 git 仓库里造场景，用 `--root` 把扫描指向那里。
不这么做的话，测试就得往本仓库里写泄露样本 —— 而本文件自己也会出现在
扫描结果里（脚本扫整个仓库），等于测试污染了自己的输入。
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LAN_ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(LAN_ROOT, "scripts", "check_privacy.py")

# 拼出来而不是写字符串字面量：这个文件本身会被隐私检查扫到，
# 直接写字面量会让它成为"泄露源"（自己绊自己）。
_LEAK_IP = "192.168" + ".9.77"
LEAK_LINE = "PEER_HOST=" + _LEAK_IP + "\n"


class ScanScopeTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.dir = self.td.name
        r = subprocess.run(["git", "-C", self.dir, "init", "-q"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"git init 失败: {r.stderr}")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")

    def git(self, *args):
        return subprocess.run(["git", "-C", self.dir, *args],
                              capture_output=True, text=True)

    def _write(self, name, content):
        path = os.path.join(self.dir, name)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _commit(self, name="base.txt", content="hello\n"):
        self._write(name, content)
        self.git("add", name)
        self.git("commit", "-qm", "base")

    def _scan(self, *args):
        p = subprocess.run(
            [sys.executable, SCRIPT, "--root", self.dir, *args],
            capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def test_untracked_file_is_scanned(self):
        """未跟踪的新文件里的真实网段必须被拦下 —— 本次修复的核心。"""
        self._commit()
        self._write("new_app.lume", LEAK_LINE)
        rc, out = self._scan()
        self.assertEqual(rc, 1, f"未跟踪文件没被扫到:\n{out}")
        self.assertIn("new_app.lume", out)
        self.assertIn(_LEAK_IP, out)

    def test_tracked_file_is_scanned(self):
        """已跟踪文件仍然要扫（回归保护，别改坏了）。"""
        self._commit("old.lume", LEAK_LINE)
        rc, out = self._scan()
        self.assertEqual(rc, 1, out)
        self.assertIn("old.lume", out)

    def test_gitignored_file_not_reported(self):
        """被 .gitignore 覆盖的文件不进仓库，报它只是噪音。

        审计日志(.jsonl)、.runtime/ 都在忽略列表里，里面全是真实 IP。
        如果这些也报，真正的泄漏会被淹掉，最后没人看这个检查。
        """
        self._commit(".gitignore", "*.log\n")
        self._write("app.log", LEAK_LINE)
        rc, out = self._scan()
        self.assertEqual(rc, 0, f"被忽略的文件不该报:\n{out}")

    def test_tracked_flag_keeps_old_behavior(self):
        """--tracked 退回旧行为：只查已跟踪。给需要的人留出口。"""
        self._commit()
        self._write("new_app.lume", LEAK_LINE)
        rc, out = self._scan("--tracked")
        self.assertEqual(rc, 0, f"--tracked 仍报了未跟踪文件:\n{out}")

    def test_staged_only_scans_index(self):
        """--staged 语义不变：只看暂存区（pre-commit 钩子依赖它）。"""
        self._commit()
        self._write("unstaged.lume", LEAK_LINE)
        rc, out = self._scan("--staged")
        self.assertEqual(rc, 0, f"--staged 扫到了未暂存文件:\n{out}")
        self.git("add", "unstaged.lume")
        rc, out = self._scan("--staged")
        self.assertEqual(rc, 1, f"--staged 没扫到已暂存文件:\n{out}")

    def test_clean_tree_passes(self):
        """没有泄露时不该误报（示例网段 192.168.1.x 是允许的）。"""
        self._commit("doc.md", "示例: PEER_HOST=192.168.1.20:18092\n")
        rc, out = self._scan()
        self.assertEqual(rc, 0, f"示例网段被误报:\n{out}")

    def test_scope_message_reflects_coverage(self):
        """通过时的提示要说清扫了什么，否则看不出覆盖范围。"""
        self._commit()
        rc, out = self._scan()
        self.assertEqual(rc, 0, out)
        self.assertIn("未跟踪", out, f"提示没说清扫描范围: {out}")
        rc, out = self._scan("--tracked")
        self.assertIn("仅已跟踪", out, f"--tracked 的提示不对: {out}")

    def test_bad_root_argument(self):
        """--root 缺参数要明确报错，而不是默默用默认根。"""
        p = subprocess.run([sys.executable, SCRIPT, "--root"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
