#!/usr/bin/env python3
"""adguard_fetch.py — 抓 AdGuard Home 查询日志 → .data/dns.json 快照。

用法: python3 scripts/adguard_fetch.py [--limit 2000]

数据流:
    AdGuard /control/querylog(最近 N 条 DNS 查询) → 按「客户端 IP」聚合
    最近 1h 内的查询(每客户端去重域名 Top5 + 拦截数)
    全家 Top10 域名排行(1h 内) → 原子写 .data/dns.json
    → Lume 出 /api/dns → 前端「域名访问」区。

配置(.env, shell env 优先):
    ADGUARD_HOST=127.0.0.1     AdGuard Home 所在主机(宿主服务, 默认本机)
    ADGUARD_PORT=10095        控制台/API 端口(宿主服务直绑, >10000 约定)
    ADGUARD_USER=admin       控制台账号(向导设的), 登录态 API 用
    ADGUARD_PASS=aglan       控制台密码

说明: 新版 AdGuard(>=0.107) API 前缀是 /control(旧版是 /v3)。本部署 users=[] 关掉了
      web 认证(局域网内部工具, 仅本机监听), 所以 /control/querylog 免登录直读。
      没装好前 /control/ 一律 302 到安装页, 脚本标记 reachable=false 降级。

AdGuard 未运行/不通: 保留旧快照并标记 reachable=false(UI 显示「未连接」), 不崩。
零第三方依赖(urllib)。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / ".data"
SNAP = DATA / "dns.json"
ENV = ROOT / ".env"
WIN = 3600          # 「近 1h」聚合窗口(秒)
TOPD = 15           # 每客户端域名 Top N(前端默认显示 5, 可展开看全部)
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
    for k in ("ADGUARD_HOST", "ADGUARD_PORT", "ADGUARD_USER", "ADGUARD_PASS"):
        if os.environ.get(k):
            vals[k] = os.environ[k]
    return vals


def _parse_ts(s) -> int:
    """把 AdGuard 的 ISO 时间(如 2026-10-16T20:45:30.347942+08:00)转 epoch 秒。

    不能直接依赖 fromisoformat: Python 3.9 只收 3/6 位小数秒, 而 AdGuard 不同版本
    会写不同位数(容器版 9 位 .860251365, 宿主版 6 位 .347942)。这里手动切分
    base+小数+时区, 小数统一截/补成 6 位再解析。解析失败返回 0。"""
    if not s:
        return 0
    import re
    from datetime import datetime
    m = re.match(r"^(.+T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$",
                 str(s).strip())
    if not m:
        return 0
    base, frac, tz = m.groups()
    frac = (frac or "")[:6].ljust(6, "0") if frac else ""
    s2 = base + (("." + frac) if frac else "") + (tz or "")
    s2 = s2.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return int(dt.timestamp())
    except Exception:
        return 0


def get_querylog(host: str, port: str, limit: int, user: str = "", pw: str = "") -> list:
    """拉 AdGuard 查询日志。新版 API 路径 /control/querylog(users 为空时无需认证;
    完成安装前 /control/ 会 302 到安装页 → 返回 HTML, json.loads 抛错, main 捕获降级)。"""
    base = f"http://{host}:{port}"
    url = f"{base}/control/querylog?skip=0&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "lume-lan"})
    if user and pw:
        # 开认证后(users 非空)必须带 Basic Auth, 否则 /control/* 一律 401。
        import base64 as _b64
        token = _b64.b64encode(f"{user}:{pw}".encode("utf-8")).decode("ascii")
        req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    return data.get("data", [])


def build(entries: list, now: int) -> dict:
    """把 AdGuard /control/querylog 查询日志聚合成 {clients:{ip:{...}}, top_domains:[...]}。

    新版 AdGuard(>=0.107) 字段:
      client        = 客户端 IP
      question.name = 被解析的域名
      time          = ISO 字符串(需解析)
      filterId      = 有值 = 被拦截; 无 = 正常
      reason        = 拦截原因文本
      status        = NOERROR / NXDOMAIN(拦截时也是 NOERROR, 靠 filterId 判断)
    """
    cutoff = now - WIN

    clients: dict[str, dict] = {}
    dom: dict[str, dict] = {}

    for e in entries:
        ts = _parse_ts(e.get("time"))
        if ts < cutoff:
            continue
        cip = e.get("client") or "unknown"
        q = e.get("question") or {}
        name = (q.get("name") or "").lower().rstrip(".")
        # 个别客户端(如钉钉)会把完整 URL 塞进 DNS 查询, 剥掉协议前缀再展示
        if name.startswith(("https://", "http://")):
            name = name.split("://", 1)[1].split("/", 1)[0]
        if not name or name == "":
            continue
        # 拦截判断: 有 filterId 且 reason 不是白名单放行 = 被规则拦。
        # 注意不能用 "Filtered" in reason —— "NotFilteredNotFound"(未拦截) 也含该子串,
        # 会把所有查询误判成拦截(旧 bug)。reason 取值(0.107+):
        #   FilteredBlackList=拦截 / FilteredWhiteList=白名单放行(不算拦) /
        #   NotFilteredNotFound=未命中规则(正常放行)
        filter_id = e.get("filterId")
        reason = e.get("reason") or ""
        blocked = bool(filter_id) and reason != "FilteredWhiteList"

        cname = ((e.get("client_info") or {}).get("name")) or ""
        c = clients.setdefault(cip, {"name": cname, "total": 0, "blocked": 0,
                                     "domains": {}, "last_ts": 0})
        c["total"] += 1
        c["last_ts"] = max(c["last_ts"], ts)
        if cname:
            c["name"] = cname
        if blocked:
            c["blocked"] += 1

        d = dom.setdefault(name, {"count": 0, "blocked": 0, "last_ts": 0})
        d["count"] += 1
        d["last_ts"] = max(d["last_ts"], ts)
        if blocked:
            d["blocked"] += 1

        cd = c["domains"].setdefault(name, {"count": 0, "blocked": 0, "last_ts": 0})
        cd["count"] += 1
        cd["last_ts"] = ts
        if blocked:
            cd["blocked"] += 1

    # 每客户端域名 Top N(拦截的排前, 其次按次数)
    for c in clients.values():
        ds = sorted(c["domains"].items(),
                    key=lambda kv: (kv[1]["blocked"], kv[1]["count"]), reverse=True)
        c["top"] = [{"domain": k, "count": v["count"], "blocked": v["blocked"], "last_ts": v["last_ts"]}
                    for k, v in ds[:TOPD]]

    topd = sorted(dom.items(),
                  key=lambda kv: (kv[1]["blocked"], kv[1]["count"]), reverse=True)
    return {
        "reachable": True,
        "fetched_at": now,
        "window_secs": WIN,
        "query_count": sum(c["total"] for c in clients.values()),
        "clients": clients,
        "client_count": len(clients),
        "top_domains": [{"domain": k, "count": v["count"], "blocked": v["blocked"], "last_ts": v["last_ts"]}
                        for k, v in topd[:TOTAL_TOP]],
    }


def main() -> None:
    cfg = load_env()
    host = cfg.get("ADGUARD_HOST") or os.environ.get("ADGUARD_HOST") or "127.0.0.1"
    port = str(cfg.get("ADGUARD_PORT") or os.environ.get("ADGUARD_PORT") or "10095")
    user = str(cfg.get("ADGUARD_USER") or os.environ.get("ADGUARD_USER") or "")
    pw = str(cfg.get("ADGUARD_PASS") or os.environ.get("ADGUARD_PASS") or "")
    limit = 2000
    for a in sys.argv[1:]:
        if a == "--limit" and len(sys.argv) > sys.argv.index(a) + 1:
            limit = int(sys.argv[sys.argv.index(a) + 1])

    now = int(time.time())
    try:
        entries = get_querylog(host, port, limit, user, pw)
        DATA.mkdir(exist_ok=True)
        payload = build(entries, now)
        tmp = SNAP.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
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
        os.chmod(tmp, 0o600)
        tmp.replace(SNAP)
        print(f"⚠ AdGuard({host}:{port}) 不可达: {ex}"
              + ("(已标记 stale, 保留旧数据)" if old else "(无旧数据)"))


if __name__ == "__main__":
    main()
