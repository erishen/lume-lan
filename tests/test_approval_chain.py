"""审批链的三方一致性测试。

为什么需要它: 同一套审批规则存在**三份**实现 ——
  1. lume-core/lib/approval.lume   (lume 侧, A2A 端点用)
  2. lume-lan/scripts/a2a_worker.py (宿主 worker 用, Python)
  3. tests/test_a2a_worker.py       (判定入口, 复用 2 的常量)

1 和 2 必须逐条一致: 一旦"lume 说轮到你了、worker 说没轮到", 任务会永久卡在
input-required, 而且日志两边都"正常", 极难排查。

本文件把同一组场景分别喂给 1 和 2, 比对结果。lume 侧通过 `lume-core --check`
+ 实际执行 .lume 脚本产出 JSON, 与 Python 侧的结果逐项比对。

跑法: make test  (需要 lume-core/bin/lume-core 可执行; 缺失时跳过而非失败)
"""
import json
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

sys.path.insert(0, os.path.join(LAN_ROOT, "scripts"))
import a2a_worker as W  # noqa: E402


# 场景：同一组操作序列，两边必须给出同样的 (ok, done, next_role, rejected)
SCENARIOS = [
    # (名字, risk, [(role, decision), ...])
    ("单级链一次通过", "low", [("human_admin", "approve")]),
    ("标准链逐级通过", "medium",
     [("agent_reviewer", "approve"), ("agent_reviewer_second", "approve"),
      ("human_admin", "approve")]),
    ("二审抢先被拒", "medium", [("agent_reviewer_second", "approve")]),
    ("operator 越权审批被拒", "medium", [("agent_operator", "approve")]),
    ("一审驳回终止", "medium", [("agent_reviewer", "reject")]),
    ("未知角色被拒", "medium", [("god", "approve")]),
    ("peer 链单级", "peer", [("agent_reviewer", "approve")]),
    ("未知风险按最严", "bogus",
     [("agent_reviewer", "approve"), ("agent_reviewer_second", "approve"),
      ("human_admin", "approve")]),
]

LUME_SCRIPT = r'''
import "lib/approval.lume" as A;
let out = [];
let i = 0;
while (i < len(CASES)) {
  let c = get(CASES, i, {});
  let chain = A.chain_for(str(get(c, "risk", "")));
  let ap = [];
  let steps = get(c, "steps", []);
  let j = 0;
  while (j < len(steps)) {
    let s = get(steps, j, {});
    let r = A.try_approve(chain, ap, str(get(s, "role", "")),
                          "t", str(get(s, "decision", "")), "");
    push(out, { ok: r.ok, done: r.done, next: r.next_role, rejected: r.rejected });
    if (r.ok) { ap = r.approvals; }
    j = j + 1;
  }
  i = i + 1;
}
print(stringify(out));
'''


def run_lume(cases):
    """在 lume-core 目录下跑脚本（这样 lib/approval.lume 的相对 import 成立）。"""
    src = "let CASES = " + json.dumps(cases, ensure_ascii=False) + ";\n" + LUME_SCRIPT
    with tempfile.NamedTemporaryFile("w", suffix=".lume", dir=CORE,
                                     delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        r = subprocess.run([LUME_CORE, path], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(r.stdout + r.stderr)
        # 取最后一行合法 JSON（lume 会附一行 note）
        for line in reversed(r.stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith("["):
                return json.loads(line)
        raise RuntimeError("no JSON in output: " + r.stdout)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@unittest.skipUnless(os.path.exists(LUME_CORE), "lume-core 二进制不存在，跳过跨实现比对")
class TestChainParity(unittest.TestCase):
    def test_python_and_lume_agree(self):
        cases = [{"risk": risk,
                  "steps": [{"role": r, "decision": d} for r, d in steps]}
                 for _, risk, steps in SCENARIOS]
        lume_out = run_lume(cases)

        idx = 0
        mismatches = []
        for name, risk, steps in SCENARIOS:
            chain = W.approval_chain(risk)
            ap = []
            for role, decision in steps:
                got = W.approval_try(chain, ap, role, "t", decision, "")
                mine = {"ok": got["ok"], "done": got["done"],
                        "next": got["next_role"], "rejected": got["rejected"]}
                theirs = lume_out[idx]
                if mine != theirs:
                    mismatches.append((name, role, mine, theirs))
                if got["ok"]:
                    ap = got["approvals"]
                idx += 1
        self.assertEqual(mismatches, [],
                         "lume 与 python 的审批链结果不一致:\n" +
                         "\n".join(f"  {m[0]} / {m[1]}: py={m[2]} lume={m[3]}"
                                   for m in mismatches))


class TestChainBehaviour(unittest.TestCase):
    """不依赖 lume 的行为断言（Python 侧自证）。"""

    def test_duty_separation(self):
        # 职责分离: 终审者不得直接执行, 执行者不得审批
        self.assertTrue(W.ROLE_ACTIONS["agent_operator"].count("execute") == 1)
        self.assertNotIn("approve", W.ROLE_ACTIONS["agent_operator"])
        self.assertIn("approve", W.ROLE_ACTIONS["human_admin"])
        self.assertNotIn("execute", W.ROLE_ACTIONS["human_admin"])

    def test_unknown_risk_is_strictest(self):
        self.assertEqual(W.approval_chain("whatever"), W.CHAIN_STANDARD)

    def test_chain_completes_only_when_all_approved(self):
        chain = W.approval_chain("medium")
        ap = []
        for role in chain[:-1]:
            r = W.approval_try(chain, ap, role, "t", "approve", "")
            self.assertTrue(r["ok"])
            self.assertFalse(r["done"])
            ap = r["approvals"]
        last = W.approval_try(chain, ap, chain[-1], "t", "approve", "")
        self.assertTrue(last["ok"])
        self.assertTrue(last["done"])
        self.assertTrue(W.approval_is_complete(chain, last["approvals"]))

    def test_parse_verdict(self):
        self.assertEqual(W.parse_verdict("agent_reviewer: approve"),
                         {"role": "agent_reviewer", "decision": "approve"})
        self.assertEqual(W.parse_verdict("human_admin: reject 不合规"),
                         {"role": "human_admin", "decision": "reject"})
        self.assertEqual(W.parse_verdict("yes"), {"role": "", "decision": "approve"})
        self.assertEqual(W.parse_verdict("好像可以"), {"role": "", "decision": ""})
        self.assertEqual(W.parse_verdict(""), {"role": "", "decision": ""})


if __name__ == "__main__":
    unittest.main()
