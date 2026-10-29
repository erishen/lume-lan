#!/usr/bin/env python3
"""peers_fetch.py — 探测局域网内的 lume peer 节点 → .data/peers.json 快照。

用法: python3 scripts/peers_fetch.py

数据流:
    对 .env 的 PEER_TARGETS 里每个节点(host:port 逗号分隔)依次调用
    /peer/health + /peer/tools + /peer/info(均需 HMAC 签名)
    → 原子写 .data/peers.json → Lume 出 /api/peers → 前端「应用互访」Tab。

为什么在宿主跑(与 scan_lan.py 同一套分工):
    lume-lan 的看板跑在 Docker 的 NAT 网段(192.168.164.x), 连宿主 loopback
    上的 peer 端口不通; 而 peer 端默认也只绑 127.0.0.1。把探测放在宿主,
    容器只读快照 —— 容器不需要出网, PEER_SHARED 也不进容器环境。

配置(.env, shell env 优先):
    PEER_SHARED        与对端共享的密钥; 留空 = 不探测(写空表, 不报错)
    PEER_TARGETS       "127.0.0.1:18152,192.168.1.20:18088"
    PEER_TARGET_TIMEOUT  单节点超时秒数(默认 5)
    PEER_STALE_SECS    快照过期阈值(默认 300)

签名与 lume-core/lib/peer.lume 完全一致(同一 key/同一 scope 规则):
    sig = HMAC-SHA256(PEER_SHARED, node \\n ts \\n nonce \\n scope)
    GET  → scope = "GET " + path
    POST → scope = "POST " + path + "\\n" + body

任一节点不通: 该节点标记 reachable=false + error, 其余照常; 整体不崩。
零第三方依赖(urllib / hmac / hashlib)。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")
OUT = os.path.join(ROOT, ".data", "peers.json")


def load_env() -> dict:
    """读 .env(无第三方依赖; shell env 优先)。"""
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
    for k in ("PEER_SHARED", "PEER_TARGETS", "PEER_TARGET_TIMEOUT",
              "PEER_STALE_SECS", "PEER_NAME"):
        if os.environ.get(k):
            vals[k] = os.environ[k]
    return vals


def sign(shared: str, node: str, ts: str, nonce: str, scope: str) -> str:
    msg = "\n".join([node, ts, nonce, scope]).encode()
    return hmac.new(shared.encode(), msg, hashlib.sha256).hexdigest()


def signed_url(shared: str, node: str, base: str, path: str) -> str:
    ts = str(int(time.time()))
    # nonce 只需每次不同; 服务端靠时间窗 + 签名, 不做 nonce 去重
    nonce = secrets.token_hex(16)
    scope = "GET " + path
    sig = sign(shared, node, ts, nonce, scope)
    return f"{base}{path}?node={node}&ts={ts}&nonce={nonce}&sig={sig}"


def get_json(shared: str, node: str, base: str, path: str, timeout: float):
    """返回 (ok, payload_or_error)。网络/JSON 失败都走 (False, msg)。"""
    url = signed_url(shared, node, base, path)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(1 << 20)
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return False, str(getattr(e, "reason", e))
    try:
        return True, json.loads(raw or b"{}")
    except ValueError:
        return False, "响应不是合法 JSON"


def probe_one(shared: str, node: str, target: str, timeout: float) -> dict:
    base = "http://" + target
    entry = {
        "target": target,
        "reachable": False,
        "name": "",
        "site": "",
        "tools": [],
        "info": {},
        "error": "",
        "fetched_at": int(time.time()),
        "latency_ms": 0,
    }
    t0 = time.time()
    ok, payload = get_json(shared, node, base, "/peer/health", timeout)
    entry["latency_ms"] = int((time.time() - t0) * 1000)
    if not ok:
        entry["error"] = payload
        return entry
    if not payload.get("ok"):
        # 签名/时间窗/未配置等问题, 对端会回 unauthorized
        entry["error"] = payload.get("error", "unauthorized")
        return entry

    entry["reachable"] = True
    entry["name"] = str(payload.get("node", ""))
    entry["site"] = str(payload.get("site", ""))

    ok, t = get_json(shared, node, base, "/peer/tools", timeout)
    if ok and t.get("ok"):
        entry["tools"] = t.get("tools", [])

    # /peer/info 是可选端点(只读节点如博客才有); 没有就当没有, 不算错
    ok, i = get_json(shared, node, base, "/peer/info", timeout)
    if ok and i.get("ok"):
        entry["info"] = i
    return entry


def main() -> int:
    env = load_env()
    shared = (env.get("PEER_SHARED") or "").strip()
    node = (env.get("PEER_NAME") or "lume-lan").strip()
    targets = [t.strip() for t in (env.get("PEER_TARGETS") or "").split(",") if t.strip()]
    try:
        timeout = float(env.get("PEER_TARGET_TIMEOUT") or 5)
    except ValueError:
        timeout = 5.0

    peers = []
    if shared and targets:
        for target in targets:
            peers.append(probe_one(shared, node, target, timeout))

    payload = {
        "enabled": bool(shared),
        "fetched_at": int(time.time()),
        # 统一数据源状态契约(与 devices/router/dns 一致)
        "reachable": True,          # 宿主本机能写快照即可达
        "stale": False,
        "error": "" if shared else "未配置 PEER_SHARED, peer 探测未启用",
        "targets": targets,
        "peers": peers,
        "up_count": sum(1 for p in peers if p["reachable"]),
        "total_count": len(peers),
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, OUT)
    print(f"wrote {OUT}: {payload['up_count']}/{payload['total_count']} peer(s) reachable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
