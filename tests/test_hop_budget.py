"""跳数预算: 防止委派成环。

为什么单独测这块: 它是**成本保护**而不是功能。出错的表现不是报错, 而是
"一直在转、一直在烧 token" —— 这类问题在功能测试里看不出来, 只有专门
构造链路才能验证。

业界参考: 一份 2026 年的复盘记录, 四 agent 的 A2A 流水线进入交接循环,
烧掉 $47,000 token 才被发现。修法就是跳数预算 + 环检测。
"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LUME = os.path.join(ROOT, "..", "lume", "bin", "lume")


def _lume_ok() -> bool:
    return os.path.isfile(LUME) and os.access(LUME, os.X_OK)


# 与 peer_node.lume 里 /peer/ask 的判定逐字对应。
# 之所以复制一份而不是 import: lume 代码不能直接被 Python 调, 而这段逻辑
# 只有 12 行, 复制比包装一层运行时更省。漂移风险由下面的注释与实际情况
# 对齐 —— 改 lume 侧时必须同步改这里, 否则测试测的是旧规则。
def check(hop: int, max_hops: int, trace: list, me: str) -> str:
    if hop >= max_hops:
        return "hops"
    if me in trace:
        return "cycle"
    return "ok"


class TestHopBudget(unittest.TestCase):
    def test_first_hop_allowed(self):
        self.assertEqual(check(0, 2, ["lan"], "scanner"), "ok")

    def test_second_hop_allowed(self):
        self.assertEqual(check(1, 2, ["lan", "scanner"], "crm"), "ok")

    def test_over_budget_rejected(self):
        # 第 3 跳(hop=2 且上限 2)必须拒 —— 这是防止"长链"的闸
        self.assertEqual(check(2, 2, ["lan", "scanner"], "crm"), "hops")

    def test_cycle_detected_immediately(self):
        # 两节点来回: A→B→A。只用计数挡不住(2 跳内可无限循环), 必须靠 trace
        self.assertEqual(check(1, 2, ["lan", "scanner"], "lan"), "cycle")

    def test_self_loop_rejected(self):
        # A→A(对端就是自己) —— 最简的环
        self.assertEqual(check(1, 2, ["lan"], "lan"), "cycle")

    def test_budget_of_one_allows_only_first(self):
        self.assertEqual(check(0, 1, ["lan"], "crm"), "ok")
        self.assertEqual(check(1, 1, ["lan"], "crm"), "hops")

    def test_empty_trace_is_first_hop(self):
        # 直接调用(不经 peer_bridge)时 trace 为空, 应视为首跳
        self.assertEqual(check(0, 2, [], "scanner"), "ok")


@unittest.skipUnless(_lume_ok(), "lume 二进制不可用, 跳过")
class TestHopLogicInLume(unittest.TestCase):
    """在真实 lume 运行时里跑一遍判定, 确认语法与语义与上面一致。"""

    def test_same_verdicts(self):
        script = os.path.join(ROOT, ".runtime", "hop_probe_tmp.lume")
        os.makedirs(os.path.dirname(script), exist_ok=True)
        with open(script, "w", encoding="utf-8") as f:
            f.write('''
func check(hop: int, max_hops: int, trace, me: string) {
  if (hop >= max_hops) { return "hops"; }
  let i = 0;
  while (i < len(trace)) {
    if (str(get(trace, i, "")) == me) { return "cycle"; }
    i = i + 1;
  }
  return "ok";
}
print(check(0, 2, ["lan"], "scanner"));
print(check(2, 2, ["lan","scanner"], "crm"));
print(check(1, 2, ["lan","scanner"], "lan"));
''')
        r = subprocess.run([LUME, script], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            r.stdout.split(),
            ["ok", "hops", "cycle"],
            "lume 侧判定与 Python 镜像不一致 —— 改任一方法务同步",
        )


if __name__ == "__main__":
    unittest.main()
