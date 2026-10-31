#!/usr/bin/env python3
"""peer_registry.py — 节点自注册: 把自己的档案写进共享目录, 而不是让人手改 N 份白名单。

── 解决的问题 ────────────────────────────────────────────────────
原来的身份注册表是每个节点各存一份 `peers-identity.json`, 内容是节点名数组:

    {"peers": ["blog", "scanner", "crm", "lan"], "profiles": {...}}

加一个新应用要**在每一个已有节点的文件里加一行**。5 个应用 = 25 次编辑,
漏掉任何一处就得到 "unknown peer 'x' (not in registry)" 的 401, 而报错
不说缺的是哪一份 —— 排障成本远高于改这一行本身。

── 做法 ─────────────────────────────────────────────────────────
每个节点启动时往**同一个目录**写 `<node>.json`:

    .data/peers.d/blog.json      {"node":"blog","port":18321,"tools":2,...}

lume-core/lib/identity.lume 的 known() 与 all_nodes() 已经改成"目录 ∪ 白名单"
取并集, 所以:
  * 新节点只要写自己的文件, 别人的配置一个字都不用改;
  * 老部署的白名单继续有效, 不需要一次性迁移。

── 边界 ─────────────────────────────────────────────────────────
这个目录**只是名册**, 不是信任根。验签用的密钥从 PEER_ROOT 派生, 所以:
  * 能写这个目录的人, 只能"登记名字", 不能伪造签名;
  * 反过来, 目录里有名字也不代表那个节点真的可信 —— 那由 PEER_ROOT 决定。
真正的权限收敛仍在 PEER_ROOT 与密钥管理上。

用法:
    python3 scripts/peer_registry.py register --name blog --port 18321 [--tools 2]
    python3 scripts/peer_registry.py list
    python3 scripts/peer_registry.py unregister --name blog
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = os.path.join(ROOT, ".data", "peers.d")


def reg_dir() -> str:
    return (os.environ.get("PEER_REG_DIR") or "").strip() or DEFAULT_DIR


def path_for(name: str) -> str:
    # 防路径穿越: 节点名只允许字母数字与 _-. (与 lume 侧的 known_in_dir 同一规则)
    if not name or not all(c.isalnum() or c in "_-." for c in name):
        raise SystemExit(f"非法节点名: {name!r} (只允许字母数字与 _-.)")
    return os.path.join(reg_dir(), name + ".json")


def do_register(args) -> int:
    d = reg_dir()
    os.makedirs(d, exist_ok=True)
    rec = {
        "node": args.name,
        "port": args.port,
        "tools": args.tools,
        # 加时间戳便于判断"这条登记是不是早就过期了"——节点换了端口却没人
        # 重跑注册时, 看时间戳就知道该重新登记。
        "registered_at": int(time.time()),
        "note": args.note or "",
    }
    p = path_for(args.name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print(f"✓ 已登记 {args.name} → {p}")
    return 0


def do_unregister(args) -> int:
    p = path_for(args.name)
    if os.path.exists(p):
        os.remove(p)
        print(f"✓ 已注销 {args.name}")
    else:
        print(f"  {args.name} 未登记")
    return 0


def do_list(_args) -> int:
    d = reg_dir()
    if not os.path.isdir(d):
        print(f"  ({d} 不存在, 没有自注册节点)")
        return 0
    rows = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                rows.append(json.load(f))
        except (OSError, ValueError):
            continue  # 坏文件跳过, 不影响其它节点
    if not rows:
        print("  (目录为空)")
        return 0
    for r in rows:
        ts = r.get("registered_at") or 0
        when = time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "?"
        print(f"  {r.get('node','?'):10s} :{r.get('port','?'):<6} "
              f"tools={r.get('tools','?')}  登记于 {when}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="lume peer 节点自注册")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("register", help="登记本节点")
    r.add_argument("--name", required=True)
    r.add_argument("--port", type=int, required=True)
    r.add_argument("--tools", type=int, default=0)
    r.add_argument("--note", default="")
    r.set_defaults(func=do_register)

    u = sub.add_parser("unregister", help="注销")
    u.add_argument("--name", required=True)
    u.set_defaults(func=do_unregister)

    l = sub.add_parser("list", help="列出已登记节点")
    l.set_defaults(func=do_list)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
