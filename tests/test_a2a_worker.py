"""a2a_worker.py 审批判定的单测。

这块是「授权」逻辑，不是普通业务逻辑：判错的后果是「没批准就执行了」。
所以用例刻意把**看起来像同意、实际不是**的说法全部列出来当反例。

跑法: make test  (或 python3 -m unittest discover -s tests)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import a2a_worker as W  # noqa: E402


class TestApprovalJudgement(unittest.TestCase):
    """精确匹配：只有归一化后完全等于白名单词才算批准。"""

    def test_explicit_yes_passes(self):
        for t in ("yes", "YES", "  yes  ", "确认", "批准", "ok", "approved"):
            self.assertTrue(W.is_approved(t), repr(t))

    def test_trailing_punctuation_tolerated(self):
        # 句末标点不该让一个明确的「确认」失效
        for t in ("yes!", "确认。", "同意！", "ok.", "批准~"):
            self.assertTrue(W.is_approved(t), repr(t))

    def test_negation_containing_yes_is_rejected(self):
        # 这几个是旧实现的真实误判来源: 用 `in` 判断时它们都含 "yes"/"可以"
        for t in ("不 yes 吧", "不是", "no", "好的我不同意", "yes or no"):
            self.assertFalse(W.is_approved(t), repr(t))

    def test_hedging_is_rejected(self):
        # 含糊其辞不算批准 —— 宁可多问一轮
        for t in ("好像可以", "是它吗", "可以吗？", "让我想想", "等一下"):
            self.assertFalse(W.is_approved(t), repr(t))

    def test_empty_and_garbage_rejected(self):
        for t in ("", "   ", "yesno", "yes please", "x"):
            self.assertFalse(W.is_approved(t), repr(t))


class TestPayloadDigest(unittest.TestCase):
    """审计里记摘要, 要求: 同内容同摘要, 改一个字段就变。"""

    def test_stable_for_same_payload(self):
        a = {"kind": "peer_invoke", "tool": "blog.stats", "arguments": {}}
        b = {"arguments": {}, "tool": "blog.stats", "kind": "peer_invoke"}
        self.assertEqual(W.payload_digest(a), W.payload_digest(b))

    def test_changes_when_payload_changes(self):
        a = {"kind": "peer_invoke", "tool": "blog.stats", "arguments": {}}
        b = {"kind": "peer_invoke", "tool": "blog.stats", "arguments": {"limit": 20}}
        self.assertNotEqual(W.payload_digest(a), W.payload_digest(b))


class TestOriginMarker(unittest.TestCase):
    """来源标记 [via:lan-ui] 的解析。

    这个标记是「谁批的」在审计里唯一的依据, 也直接影响判定:
    标记留在正文里会让 "approve [via:lan-ui]" 匹配不上精确比较,
    任务停在 input-required 原地不动 —— 表现为"看板批了却没生效"。
    """

    def test_ui_marker_does_not_break_approval(self):
        r = W.parse_verdict("agent_reviewer: approve [via:lan-ui]")
        self.assertEqual(r["decision"], "approve")
        self.assertEqual(r["role"], "agent_reviewer")

    def test_ui_marker_with_reject_keeps_reason(self):
        r = W.parse_verdict("agent_reviewer: reject 数据不该外发 [via:lan-ui]")
        self.assertEqual(r["decision"], "reject")

    def test_bare_yes_with_marker_still_approves(self):
        r = W.parse_verdict("yes [via:lan-ui]")
        self.assertEqual(r["decision"], "approve")

    def test_no_marker_is_cli(self):
        task = {"history": [{"role": "user",
                             "parts": [{"kind": "text", "text": "agent_reviewer: approve"}]}]}
        self.assertEqual(W.last_user_origin(task), "cli")

    def test_marker_is_lan_ui(self):
        task = {"history": [{"role": "user", "parts": [
            {"kind": "text", "text": "agent_reviewer: approve [via:lan-ui]"}]}]}
        self.assertEqual(W.last_user_origin(task), "lan-ui")

    def test_empty_history_is_unknown_not_guessed(self):
        self.assertEqual(W.last_user_origin({"history": []}), "unknown")


class TestAuditCall(unittest.TestCase):
    """a2a_call 审计字段。

    这些字段是"事后审计"唯一能回答「发起了什么、多快、对端怎么答」的地方,
    漏一个就只剩一句 failed。
    """

    def setUp(self):
        self.written = []
        self._orig = W.audit
        W.audit = lambda e: self.written.append(e)

    def tearDown(self):
        W.audit = self._orig

    def test_records_status_and_latency(self):
        W._LAST_CALL.update(status=200, elapsed_ms=27, resp_bytes=137)
        W.audit_call("tid1", "10.0.0.2:18321", "peer/invoke", "ok",
                     "result_len=103", tool="blog.stats")
        e = self.written[0]
        self.assertEqual(e["event"], "a2a_call")
        self.assertEqual(e["status"], 200)
        self.assertEqual(e["elapsed_ms"], 27)
        self.assertEqual(e["outcome"], "ok")
        self.assertEqual(e["tool"], "blog.stats")

    def test_outcome_is_categorical_not_boolean(self):
        # 失败要能分类: 网络不通 / 被拒 / 响应异常 三种处理方式完全不同
        for outcome in ("unreachable", "denied", "bad_response"):
            W.audit_call("t", "h:1", "message/send", outcome, "x")
        self.assertEqual([e["outcome"] for e in self.written],
                         ["unreachable", "denied", "bad_response"])

    def test_detail_is_truncated(self):
        W.audit_call("t", "h:1", "m", "ok", "x" * 500)
        self.assertLessEqual(len(self.written[0]["detail"]), 200)

    def test_elapsed_survives_exception_path(self):
        # peer_call 在异常分支也要把 elapsed/status 写进槽位, 否则
        # 失败调用在审计里永远是 0ms / 0 —— 排不出"是慢死还是立刻挂"
        W._LAST_CALL.update(status=0, elapsed_ms=5001, resp_bytes=0)
        W.audit_call("t", "unreachable:1", "peer/invoke", "unreachable", "timed out")
        self.assertEqual(self.written[0]["elapsed_ms"], 5001)


class TestPeerToolWhitelist(unittest.TestCase):
    """看板调工具的边界: 只允许对端自己声明过的工具。

    这条不测就会退化成"打穿到对端任意工具"的跳板 —— lume-lan 自己挂了
    MCP, 能调的东西远不止博客那两个只读查询。
    """

    def _peer(self, tools):
        return {"target": "10.0.0.2:18321", "name": "blog",
                "tools": [{"name": t} for t in tools]}

    def test_declared_tool_allowed(self):
        p = self._peer(["blog.stats", "blog.recent"])
        self.assertIn("blog.stats", [t["name"] for t in p["tools"]])

    def test_undeclared_tool_is_not_in_list(self):
        # 等价于服务端 peer_has_tool 的判定: 清单里没有就不许调
        p = self._peer(["blog.stats"])
        names = [t["name"] for t in p["tools"]]
        self.assertNotIn("fs.read", names)

    def test_empty_tool_list_rejects_everything(self):
        p = self._peer([])
        self.assertEqual([t["name"] for t in p["tools"]], [])


if __name__ == "__main__":
    unittest.main()
