#!/usr/bin/env python3
"""adguard_fetch.py — 抓 AdGuard Home 查询日志 → .data/dns.json 快照。

用法: python3 scripts/adguard_fetch.py [--limit 2000]

数据流:
    AdGuard /v3/querylog(最近 N 条 DNS 查询) → 按「客户端 IP」聚合
    最近 1h 内的查询(每客户端去重域名 Top5 + 拦截数)
    全家 Top10 域名排行(1h 内) → 原子写 .data/dns.json
    → Lume 出 /api/dns → 前端「域名访问」区。

配置(.env, shell env 优先):
    ADGUARD_HOST=127.0.0.1     AdGuard Home 容器宿主端口映射(ADGUARD_PORT, 默认 10095)所在主机
    ADGUARD_PORT=10095        宿主发布端口(容器内固定 3000, 宿主映射到 10095, >10000 约定)

AdGuard 未运行/不通: 保留旧快照并标记 reachable=false(UI 显示「未连接」), 不崩。
零第三方依赖(urllib)。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / ".data"
SNAP = DATA / "dns.json"
ENV = ROOT / ".env"
WIN = 3600          # 「近 1h」聚合窗口(秒)
TOPD = 5            # 每客户端域名 Top N
TOTAL_TOP = 10      # 全家域名排行 Top N


def load_env() -> dict:
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
    for k in ("ADGUARD_HOST", "ADGUARD_PORT"):
        if os.environ.get(k):
            vals[k] = os.environ[k]
    return vals


def get_querylog(host: str, port: str, limit: int) -> list:
    url = f"http://{host}:{port}/v3/querylog?skip=0&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "lume-lan"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("data", [])


def build(entries: list, now: int) -> dict:
    """把原始查询日志聚合成 {clients:{ip:{...}}, top_domains:[...]}。"""
    cutoff = now - WIN
    fresh = [e for e in entries if int(e.get("time") or 0) >= cutoff]
    clients: dict[str, dict] = {}
    dom: dict[str, dict] = {}

    for e in fresh:
        cip = e.get("ip") or e.get("client_ip") or "unknown"
        name = (e.get("name") or "").lower()
        if not name or name == "." :
            continue
        blocked = e.get("status") in ("filtered", "blocked", "dns_rejected", "filtered_dnssec")
        cname = e.get("client_name") or ""
        c = clients.setdefault(cip, {"name": cname, "total": 0, "blocked": 0,
                                     "domains": {}, "last_ts": 0})
        c["total"] += 1
        c["last_ts"] = max(c["last_ts"], int(e.get("time") or 0))
        if cname:
            c["name"] = cname
        if blocked:
            c["blocked"] += 1
        d = dom.setdefault(name, {"count": 0, "blocked": 0, "last_ts": 0})
        d["count"] += 1
        d["last_ts"] = max(d["last_ts"], int(e.get("time") or 0))
        if blocked:
            d["blocked"] += 1
        cd = c["domains"].setdefault(name, {"count": 0, "blocked": 0, "last_ts": 0})
        cd["count"] += 1
        cd["last_ts"] = c["last_ts"]
        if blocked:
            cd["blocked"] += 1

    # 每客户端域名 Top N(按次数, 拦截的排前)
    for c in clients.values():
        ds = sorted(c["domains"].items(), key=lambda kv: (kv[1]["blocked"], kv[1]["count"]), reverse=True)
        c["top"] = [{"domain": k, "count": v["count"], "blocked": v["blocked"], "last_ts": v["last_ts"]}
                    for k, v in ds[:TOPD]]

    topd = sorted(dom.items(), key=lambda kv: (kv[1]["blocked"], kv[1]["count"]), reverse=True)
    return {
        "reachable": True,
        "fetched_at": now,
        "window_secs": WIN,
        "query_count": len(fresh),
        "clients": clients,
        "client_count": len(clients),
        "top_domains": [{"domain": k, "count": v["count"], "blocked": v["blocked"], "last_ts": v["last_ts"]}
                        for k, v in topd[:TOTAL_TOP]],
    }


def main() -> None:
    cfg = load_env()
    host = cfg.get("ADGUARD_HOST") or os.environ.get("ADGUARD_HOST") or "127.0.0.1"
    port = str(cfg.get("ADGUARD_PORT") or os.environ.get("ADGUARD_PORT") or "10095")
    limit = 2000
    for a in sys.argv[1:]:
        if a == "--limit" and len(sys.argv) > sys.argv.index(a) + 1:
            limit = int(sys.argv[sys.argv.index(a) + 1])

    now = int(time.time())
    try:
        entries = get_querylog(host, port, limit)
        DATA.mkdir(exist_ok=True)
        payload = build(entries, now)
        tmp = SNAP.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(SNAP)
        print(f"✓ .data/dns.json: 近 1h {payload['query_count']} 条查询, "
              f"{payload['client_count']} 客户端, Top域 {payload['top_domains'][0]['domain'] if payload['top_domains'] else '—'}")
    except Exception as ex:
        # 保留旧快照 + 标记不可达(UI 显示「AdGuard 未连接」), 旧快照继续可用
        old = None
        if SNAP.exists():
            try:
                old = json.loads(SNAP.read_text())
            except Exception:
                old = None
        mark = {"reachable": False, "fetched_at": now, "error": f"{type(ex).__name__}: {ex}",
                 "clients": old.get("clients", {}) if old else {},
                 "top_domains": old.get("top_domains", []) if old else [],
                 "query_count": 0, "client_count": 0, "window_secs": WIN}
        if old:
            mark["stale"] = True
            mark["stale_since"] = old.get("fetched_at", 0)
        tmp = SNAP.with_suffix(".tmp")
        tmp.write_text(json.dumps(mark, ensure_ascii=False), encoding="utf-8")
        tmp.replace(SNAP)
        print(f"⚠ AdGuard({host}:{port}) 不可达: {ex}"
              + ("(已标记 stale, 保留旧数据)" if old else "(无旧数据)"))


if __name__ == "__main__":
    main()
