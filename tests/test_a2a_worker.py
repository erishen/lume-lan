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


if __name__ == "__main__":
    unittest.main()
