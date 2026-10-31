"""peer_bridge 的客户端侧工具可见性测试。

两个词表必须对齐：服务端（lume 的 peer_tool_policy）决定"能不能调"，
客户端（本桥）决定"要不要展示给 agent"。它们各自维护时容易漂移，
而漂移的方向通常是"客户端以为能调、服务端拒绝" —— agent 会反复试一个
永远失败的工具，把时间花在重试上。

所以现在服务端在 /peer/tools 里返回 `peer_callable` 标注，客户端优先采信它。
本用例守的就是这条：**服务端说不行的，客户端不能展示**。
"""
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LAN_ROOT = os.path.dirname(HERE)
BRIDGE = os.path.join(LAN_ROOT, "scripts", "peer_bridge.py")


def _load_bridge():
    """加载 peer_bridge（它是 stdio MCP server，import 时必须不启动循环）。"""
    os.environ.setdefault("PEER_TARGETS", "127.0.0.1:1")  # 不可达，避免真去探测
    os.environ.setdefault("PEER_ROOT", "test-root-not-real")
    spec = importlib.util.spec_from_file_location("peer_bridge_under_test", BRIDGE)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


@unittest.skipUnless(os.path.exists(BRIDGE), "peer_bridge.py 不存在")
class ToolVisibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load_bridge()

    def test_server_marked_not_callable_is_hidden(self):
        """服务端标 peer_callable=False 的工具，客户端必须隐藏。

        这是本次新增的语义：服务端才是权威（它知道自己配了哪些 MCP、
        哪些被策略拦下）。
        """
        m = self.m
        # 无论本地 EXPOSE 配成什么，服务端说不行就不行
        for expose in ("all", "readonly", "fs,memory"):
            m.EXPOSE = expose
            self.assertFalse(
                m.tool_allowed("fs__write_file", peer_callable=False),
                f"EXPOSE={expose} 时仍展示了服务端已拒绝的工具",
            )

    def test_server_marked_callable_is_respected(self):
        """服务端标可调用的，客户端在 all 模式下应放行。"""
        m = self.m
        old = m.EXPOSE
        try:
            m.EXPOSE = "all"
            self.assertTrue(m.tool_allowed("memory__read_graph", peer_callable=True))
        finally:
            m.EXPOSE = old

    def test_legacy_peer_without_field_falls_back(self):
        """旧版对端没有 peer_callable 字段时，退回本地词表（向后兼容）。"""
        m = self.m
        old = m.EXPOSE
        try:
            m.EXPOSE = "readonly"
            # 没有标注 → peer_callable=None → 走 DENY_TOKENS
            self.assertFalse(m.tool_allowed("fs__write_file", None))
            self.assertTrue(m.tool_allowed("fs__read_file", None))
        finally:
            m.EXPOSE = old

    def test_all_mode_still_blocks_when_server_says_no(self):
        """EXPOSE=all 是"我这边不限制"，不是"无视对端策略"。

        这两件事容易混：all 表达的是客户端的宽松意愿，对端拒绝仍是拒绝。
        """
        m = self.m
        old = m.EXPOSE
        try:
            m.EXPOSE = "all"
            self.assertFalse(m.tool_allowed("git__push", peer_callable=False))
            self.assertTrue(m.tool_allowed("git__status", peer_callable=True))
        finally:
            m.EXPOSE = old


if __name__ == "__main__":
    unittest.main()
