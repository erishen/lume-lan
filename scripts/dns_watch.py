#!/usr/bin/env python3
"""dns_watch.py — 实时监听 AdGuard 查询日志, 打印「新出现的客户端 IP」。

用途: 你在某台设备(手机/平板)上随便刷个网页, 终端立刻显示这台设备的 IP
进没进 AdGuard。用来定位「DNS 到底走没走 AdGuard」这类问题。

零依赖(只读宿主 ADGUARD_HOST:ADGUARD_PORT/control/querylog, 关认证后免登录)。
Ctrl+C 退出。已知客户端缓存在 .runtime/dns_watch_seen.json, 重启不重复刷。

区分:
  192.168.1.x   = 真实局域网设备(手机/平板等设备, 你要找的就是这些)
  192.168.164.1 = Docker NAT 网段(示例) 里其他容器的 DNS 转发流量(宿主 AdGuard 下仅此一种合并来源)
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
SEEN = ROOT / ".runtime" / "dns_watch_seen.json"
INTERVAL = int(os.environ.get("DNS_WATCH_SECS", "3"))


def load_env():
    vals = {}
    try:
        for line in open(ENV, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return vals


def get_querylog(host, port, limit=3000):
    url = f"http://{host}:{port}/control/querylog?skip=0&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "lume-lan"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read()).get("data", [])


def main():
    cfg = load_env()
    host = cfg.get("ADGUARD_HOST") or "127.0.0.1"
    port = str(cfg.get("ADGUARD_PORT") or "10095")

    SEEN.parent.mkdir(exist_ok=True)
    prev = {}
    if SEEN.exists():
        try:
            prev = json.loads(SEEN.read_text())
        except Exception:
            prev = {}

    print(f"▶ 监听 AdGuard({host}:{port}) 客户端 …  现在去任一台设备上随便刷个网页, 下面会实时打印新出现的 IP。Ctrl+C 退出")
    try:
        while True:
            entries = get_querylog(host, port)
            # 每轮从 entries 重建聚合(prev 只用来判断「是否新出现的客户端」)
            seen: dict = {}
            # AdGuard 拦截判定: filterId 非空, 或 reason 是拦截类(排除 "NotFiltered*" 假阳性)
            block_reasons = ("FilteredBlackList", "FilteredWhitelist", "FilteredUserList",
                             "SafeSearch", "BlockedByRebind", "BlockedClientDisallowed")
            for e in entries:
                cip = e.get("client") or "?"
                q = (e.get("question") or {}).get("name", "").lower().rstrip(".")
                blocked = bool(e.get("filterId")) or (e.get("reason") or "") in block_reasons
                d = seen.setdefault(cip, {"n": 0, "dom": set(), "blocked": 0, "sample": ""})
                d["n"] += 1
                d["blocked"] += int(blocked)
                if q:
                    d["dom"].add(q)
                    d["sample"] = q
            # 发现新客户端(不在 prev 里) → 打印
            for cip, d in seen.items():
                if cip not in prev:
                    nat = "（Mac/Docker NAT，你自己的测试流量）" if cip.startswith("192.168.164") else "（★ 真实设备！DNS 已走 AdGuard）"
                    doms = sorted(d["dom"], key=lambda x: -1)
                    print(f"\n🆕 新客户端 {cip}  {d['n']} 次查询, 拦 {d['blocked']}" + nat)
                    if d["sample"]:
                        print(f"    最近域名: {', '.join(list(d['dom'])[:6])}")
            prev = {k: {"n": v["n"]} for k, v in seen.items()}
            SEEN.write_text(json.dumps(prev))
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print("\n已停止。")
    except Exception as ex:
        print(f"⚠ 拉取 AdGuard 失败: {ex}（确认 AdGuard 容器在跑: make adguard-up）")
        sys.exit(1)


if __name__ == "__main__":
    main()
