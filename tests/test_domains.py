"""领域 → 审批链 的单测。

重点验证三件事：
  1. 二审确实来自**另一个领域**（不是同领域更高级的专家）
  2. 未登记/登记错 一律兜底到 human_admin，绝不无人审
  3. lume 与 Python 两份实现给出同一结果
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

REGISTRY = {
    "version": 1,
    "domains": {
        "stock-data": {
            "first": {"node": "scanner", "why": "懂指标"},
            "second": {"node": "crm", "why": "跨域复核"},
        },
        "content": {
            "first": {"node": "blog", "why": "内容宿主"},
            "second": {"node": "talentlens", "why": "隐私视角"},
        },
        "only_second": {
            "second": {"node": "crm", "why": "缺一审, 应兜底"},
        },
        "empty": {},
    },
}


class DomainCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = W.DOMAIN_REGISTRY
        self.path = os.path.join(self.tmp.name, "domains.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(REGISTRY, f, ensure_ascii=False)
        W.DOMAIN_REGISTRY = self.path

    def tearDown(self):
        W.DOMAIN_REGISTRY = self._orig
        self.tmp.cleanup()


class TestDomainChain(DomainCase):
    def test_chain_shape(self):
        c = W.chain_for_domain("stock-data")
        self.assertEqual([x["stage"] for x in c], ["first", "second", "final"])
        self.assertEqual([x["role"] for x in c],
                         ["agent_reviewer", "agent_reviewer_second", "human_admin"])
        self.assertEqual(c[0]["node"], "scanner")
        self.assertEqual(c[1]["node"], "crm")

    def test_second_is_cross_domain(self):
        # 领域映射里一审二审的节点必须不同 —— 同节点等于自审
        for name, d in REGISTRY["domains"].items():
            f, s = d.get("first"), d.get("second")
            if f and s:
                self.assertNotEqual(f["node"], s["node"],
                                    f"{name}: 一审二审是同一个节点, 失去复核意义")

    def test_unregistered_falls_back_to_human(self):
        c = W.chain_for_domain("never-registered")
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["role"], "human_admin")
        self.assertEqual(c[0]["stage"], "final")

    def test_second_only_is_invalid(self):
        # 只有 second 没 first: 语义不成立, 必须兜底
        c = W.chain_for_domain("only_second")
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["role"], "human_admin")

    def test_empty_domain_object(self):
        c = W.chain_for_domain("empty")
        self.assertEqual(c[0]["role"], "human_admin")

    def test_missing_registry_file(self):
        W.DOMAIN_REGISTRY = os.path.join(self.tmp.name, "nope.json")
        c = W.chain_for_domain("stock-data")
        self.assertEqual(c[0]["role"], "human_admin")

    def test_corrupt_registry(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        c = W.chain_for_domain("stock-data")
        self.assertEqual(c[0]["role"], "human_admin")


LUME_SCRIPT = r'''
import "%s/domains.lume" as D;
let out = {};
let ds = ["stock-data", "content", "only_second", "empty", "never-registered"];
let i = 0;
while (i < len(ds)) {
  let c = D.chain_for_domain(str(get(ds, i, "")));
  let roles = [];
  let j = 0;
  while (j < len(c)) {
    push(roles, str(get(get(c, j, {}), "role", "")));
    j = j + 1;
  }
  put(out, str(get(ds, i, "")), roles);
  i = i + 1;
}
print(stringify(out));
''' % os.path.join(CORE, "lib")


@unittest.skipUnless(os.path.exists(LUME_CORE), "lume-core 二进制不存在，跳过")
class TestParity(DomainCase):
    def test_lume_and_python_agree(self):
        # lume 侧读 .data/domains.json（相对路径）, 所以把 registry 放成那个名字再跑
        src = LUME_SCRIPT
        # 写系统临时目录, 不写进 lume-core —— 之前写 CORE 目录, 一旦 unlink
        # 失败就留下 tmp*.lume 污染仓库(实际发生过 12 个)。
        with tempfile.NamedTemporaryFile("w", suffix=".lume",
                                         delete=False, encoding="utf-8") as f:
            f.write(src)
            script = f.name
        reg_path = self.path      # setUp 里写好的临时 registry
        try:
            # lume 的 read_file 按 **CWD** 解析相对路径, 而 lume-core 二进制
            # 要被从 lume-core 目录启动才能 import "lib/..."; 两者必须一致,
            # 否则模块静默回退兜底链 —— 这正是本用例第一次跑出来的问题。
            env = dict(os.environ)
            # 用 env 显式指定 registry 路径: 别再依赖 cwd(之前靠 cwd=CORE 凑巧
            # 生效, 那种隐式依赖一旦目录变了就静默走兜底链)。
            env["DOMAIN_REGISTRY"] = reg_path
            r = subprocess.run([LUME_CORE, script], capture_output=True,
                               text=True, timeout=60, cwd=CORE, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            line = next(l for l in reversed(r.stdout.strip().splitlines())
                        if l.strip().startswith("{"))
            lume_out = json.loads(line)
        finally:
            try:
                os.unlink(script)
            except OSError:
                pass


        for dom in ("stock-data", "content", "only_second", "empty",
                    "never-registered"):
            mine = [x["role"] for x in W.chain_for_domain(dom)]
            self.assertEqual(mine, lume_out[dom],
                             f"领域 {dom}: python={mine} lume={lume_out[dom]}")


if __name__ == "__main__":
    unittest.main()
