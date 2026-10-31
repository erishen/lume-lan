"""`/peer/invoke` 工具白名单的单测。

背景：lume-lan 背后是能调**任意 MCP 工具**的通用 agent。不设限的话，持有
共享凭据的对端可以调 `fs__write_file` / `memory__delete_*` —— 等于把本机
写权限和记忆存储暴露给整个信任域。

这些用例是**安全边界**的护栏：失败意味着「对端能改你的状态」。

设计演进（值得记住，因为它踩了两次）：
  第一版把所有危险动词平铺成一张表，结果两头出错 ——
    * 漏判：`memory__create_entities` / `memory__add_observations` 是写操作，
      但 create/add 不在表里，对端能往记忆里塞假事实。
    * 误伤：加上 create/add 后，`pptx__create` / `pptx__add_slide` 这些正常
      文档生成一起被拒，把可用功能变成不可用。
  两类的差别不在动词，而在**改的是什么状态**：
    - 记忆 / git / fs / 代码执行 → 跨调用留存，且范围不受控
    - 受限命名空间内的文件生成 → 影响面有界
  所以现在分两级：STRICT 命名空间默认拒（只读白名单例外），SCOPED 只禁
  破坏性动作。

测的是**真实实现**：从 peer_tool_policy.lume 里取 deny_reason，而不是在
测试里复制一份逻辑。复制的那份会随实现漂移 —— 测试仍然全绿，但守的是一份
已经不存在的规则。
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
POLICY = os.path.join(LAN_ROOT, "src", "peer_tool_policy.lume")

HAS_CORE = os.path.exists(LUME_CORE)

# 跨边界状态：必须拒绝（除只读白名单）
STRICT_DENY = [
    # 记忆写入 —— 塞进去的假事实会被后续推理当真
    "memory__create_entities",
    "memory__add_observations",
    "memory__create_relations",
    "memory__delete_entities",
    "memory__delete_observations",
    "memory__clear_all",
    # 文件写入（MCP_FS_ROOT 可能被配得很宽，不能假设是沙箱）
    "fs__write_file",
    "fs__edit_file",
    "fs__append_file",
    "fs__mkdir_p",
    "fs__move_path",
    "fs__delete",
    "fs__chmod",
    # 仓库历史
    "git__commit",
    "git__push",
    "git__reset",
    "git__checkout",
    "git__merge",
    # 任意代码执行
    "code__run",
    "exec__python",
    "shell__bash",
    "shell_exec",
    "sql__execute",
    # 容器
    "docker__run",
    "docker__exec",
]

# 只读：必须放行（否则 peer 面收窄到没用）
ALLOW = [
    "memory__read_graph",
    "memory__search_nodes",
    "memory__open_nodes",
    "fs__read_file",
    "fs__list_dir",
    "fs__search_files",
    "git__status",
    "git__log",
    "git__diff",
    "docker__ps",
    "crm__query",
    "blog__stats",
    "scanner__picks",
    "lan__overview",
    "web_search",
    "web_extract",
    "sql__select",
    "crm__export",
]

# 受限命名空间内的生成：允许创建/追加，禁止覆盖删除
SCOPED_ALLOW = ["pptx__create", "pptx__add_slide", "pptx__export",
                "docx__render", "xlsx__read", "pdf__extract", "image__generate"]
SCOPED_DENY = ["pptx__delete_slide", "docx__overwrite", "xlsx__write_cell"]


def _verdicts(names):
    """对一批工具名求 deny_reason，返回 {name: reason}（空串=放行）。"""
    payload = json.dumps(names, ensure_ascii=False)
    script = f'''
import "{POLICY}" as p;
let names = json("{payload.replace(chr(34), chr(92) + chr(34))}");
let i = 0;
while (i < len(names)) {{
  let n = str(get(names, i, ""));
  print(n + "\\t" + p.deny_reason(n));
  i = i + 1;
}}
'''
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "probe.lume")
        with open(path, "w", encoding="utf-8") as f:
            f.write(script)
        bin_ = LUME_CORE if HAS_CORE else os.path.join(WORK, "lume", "bin", "lume")
        p = subprocess.run([bin_, path], capture_output=True, text=True, timeout=180)
        out = {}
        for line in p.stdout.splitlines():
            if "\t" in line:
                k, v = line.split("\t", 1)
                out[k] = v.strip()
        return p.returncode, out, p.stderr


@unittest.skipUnless(HAS_CORE, "lume-core 二进制不存在，跳过")
class ToolPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        all_names = STRICT_DENY + ALLOW + SCOPED_ALLOW + SCOPED_DENY
        rc, verdicts, err = _verdicts(all_names)
        cls.rc, cls.verdicts, cls.err = rc, verdicts, err

    def test_policy_module_loads(self):
        """策略模块能独立 import（不带 server{}，不需要起进程）。"""
        self.assertEqual(self.rc, 0,
                         f"装载 peer_tool_policy.lume 失败: {self.err[:400]}")
        self.assertTrue(self.verdicts, f"没有拿到判定结果: {self.err[:400]}")

    def test_cross_boundary_writes_denied(self):
        """跨边界状态的写入必须拒 —— 这是本用例存在的全部理由。"""
        leaked = [n for n in STRICT_DENY if not self.verdicts.get(n)]
        self.assertEqual(
            leaked, [],
            "以下工具被放行，对端可借此修改本机持久状态: " + ", ".join(leaked))

    def test_readonly_tools_allowed(self):
        blocked = {n: self.verdicts.get(n) for n in ALLOW if self.verdicts.get(n)}
        self.assertEqual(blocked, {},
                         "以下只读工具被误拒: " + json.dumps(blocked, ensure_ascii=False))

    def test_scoped_generation_allowed(self):
        """文档生成类必须可用 —— 第一版把它们一起误拒了。"""
        blocked = {n: self.verdicts.get(n) for n in SCOPED_ALLOW if self.verdicts.get(n)}
        self.assertEqual(blocked, {},
                         "以下生成工具被误拒: " + json.dumps(blocked, ensure_ascii=False))

    def test_scoped_destructive_denied(self):
        """但覆盖/删除既有产物仍要拦。"""
        leaked = [n for n in SCOPED_DENY if not self.verdicts.get(n)]
        self.assertEqual(leaked, [],
                         "以下破坏性操作被放行: " + ", ".join(leaked))

    def test_reason_names_tool_and_reason(self):
        """拒绝理由要能定位问题，不能只是 'denied'。

        对端看到的就是这条 error 文本；说不清是"策略拦的"还是"工具不存在"，
        排障会一路怀疑网络。
        """
        rc, verdicts, err = _verdicts(["fs__write_file", "git__push"])
        self.assertEqual(rc, 0, err[:300])
        for name, reason in verdicts.items():
            self.assertTrue(reason, f"{name} 没有被拒")
            self.assertIn(name, reason, f"拒绝理由没点名工具: {reason}")
            self.assertGreater(len(reason), len(name) + 20,
                               f"拒绝理由太笼统: {reason}")

    def test_unknown_name_in_strict_namespace_denied(self):
        """严格命名空间里没见过的名字，默认拒而不是默认放。

        因为这些工具一旦是写操作，后果跨会话留存 —— 不给"猜错"的空间。
        """
        rc, verdicts, err = _verdicts(["memory__some_new_thing", "git__whatever"])
        self.assertEqual(rc, 0, err[:300])
        for name, reason in verdicts.items():
            self.assertTrue(reason, f"{name} 应当被默认拒绝（fail-closed）")


if __name__ == "__main__":
    unittest.main()
