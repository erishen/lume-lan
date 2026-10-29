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
# A2A 任务与审计的来源节点(通常是 blog)。用 PEER_STATE_DIRS 指定多个,
# 逗号分隔; 每个目录里应有 a2a-tasks/ 与 a2a-audit.jsonl。
# 为什么要它: 任务状态与审计是**执行方**写的, 看板在另一台/另一仓,
# 只能按目录读 —— 这正是"谁有权限谁采集"的延续。


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
              "PEER_STALE_SECS", "PEER_NAME", "PEER_STATE_DIRS", "PEER_ROOT"):
        if os.environ.get(k):
            vals[k] = os.environ[k]
    return vals


# 身份模式: 配了 PEER_ROOT 就按节点派生密钥签名(与 lume-core/lib/identity.lume
# 一致)。不这样做的话, 对端一旦启用身份层, 探测就全部 401 —— 而 UI 上只显示
# "不可达", 看不出是身份问题。
PEER_ROOT = ""


def key_for(node: str) -> str:
    return hmac.new(PEER_ROOT.encode(), f"peer:{node}".encode(),
                    hashlib.sha256).hexdigest()


def sign(shared: str, node: str, ts: str, nonce: str, scope: str) -> str:
    msg = "\n".join([node, ts, nonce, scope]).encode()
    key = key_for(node) if PEER_ROOT else shared
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


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


AUDIT_TAIL = 200          # 审计只取尾部: 全量可能几 MB, 看板只要最近几条


def scan_tasks(state_dirs) -> list:
    """扫各来源目录下的 a2a-tasks/*.json, 汇总成待办与最近完成。

    只取 UI 需要的字段, 不把整个任务(含 history/payload)塞给前端 ——
    任务里可能有别家应用的数据, 看板没必要也不应该全部拿到。
    """
    out = []
    for d in state_dirs:
        tdir = os.path.join(d, "a2a-tasks")
        if not os.path.isdir(tdir):
            continue
        for fn in sorted(os.listdir(tdir)):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(tdir, fn), encoding="utf-8") as f:
                    t = json.load(f)
            except (OSError, ValueError):
                continue
            st = t.get("status") or {}
            state = str(st.get("state", ""))
            if state in ("completed", "failed", "canceled", "rejected"):
                continue      # 终结态不进取数(否则列表会被历史淹掉)
            payload = (t.get("metadata") or {}).get("payload") or {}
            chain = payload.get("_chain") or []
            approvals = payload.get("_approvals") or []
            out.append({
                "id": str(t.get("id", "")),
                "state": state,
                "tool": str(payload.get("tool", "")),
                "target": str(payload.get("target", "")),
                "domain": str(payload.get("domain", "")),
                "policy": [{"role": str(c.get("role", "")),
                            "node": str(c.get("node", "")),
                            "stage": str(c.get("stage", ""))}
                           for c in chain if isinstance(c, dict)],
                "approved": [str(a.get("role", "")) for a in approvals
                             if isinstance(a, dict)],
                # 当前该谁审: 由 lume 侧的 message 给出人话, 这里不重复推导
                "waiting_for": str(((st.get("message") or {}).get("parts") or [{}])[0]
                                   .get("text", ""))[:160],
                "since": int(st.get("timestamp") or 0),
                "source": os.path.basename(os.path.dirname(d)),
            })
    out.sort(key=lambda x: x.get("since") or 0, reverse=True)
    return out


def scan_audit(state_dirs, limit: int = AUDIT_TAIL) -> list:
    """读审计尾部。JSONL 每行一条; 坏行跳过(审计文件可能是半截写入)。"""
    rows = []
    for d in state_dirs:
        path = os.path.join(d, "a2a-audit.jsonl")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()[-limit:]
        except OSError:
            continue
        src = os.path.basename(os.path.dirname(d))
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            rows.append({
                "at": int(e.get("at") or 0),
                "event": str(e.get("event", "")),
                "task_id": str(e.get("task_id", "")),
                "role": str(e.get("role") or e.get("by_role") or ""),
                "required_role": str(e.get("required_role", "")),
                "claimed_role": str(e.get("claimed_role", "")),
                "decision": str(e.get("decision") or e.get("state") or ""),
                "reason": str(e.get("reason", ""))[:120],
                "source": src,
            })
    rows.sort(key=lambda x: x.get("at") or 0, reverse=True)
    return rows[:limit]


def main() -> int:
    env = load_env()
    global PEER_ROOT
    shared = (env.get("PEER_SHARED") or "").strip()
    PEER_ROOT = (env.get("PEER_ROOT") or "").strip()
    node = (env.get("PEER_NAME") or "lume-lan").strip()
    targets = [t.strip() for t in (env.get("PEER_TARGETS") or "").split(",") if t.strip()]
    try:
        timeout = float(env.get("PEER_TARGET_TIMEOUT") or 5)
    except ValueError:
        timeout = 5.0

    peers = []
    if (shared or PEER_ROOT) and targets:
        for target in targets:
            peers.append(probe_one(shared, node, target, timeout))

    # A2A 任务与审计: 从 PEER_STATE_DIRS 指定的目录读(执行方写, 看板读)
    state_dirs = []
    for d in (env.get("PEER_STATE_DIRS") or "").split(","):
        d = d.strip()
        if not d:
            continue
        state_dirs.append(d if os.path.isabs(d) else os.path.join(ROOT, d))

    payload = {
        "enabled": bool(shared or PEER_ROOT),
        "fetched_at": int(time.time()),
        # 统一数据源状态契约(与 devices/router/dns 一致)
        "reachable": True,          # 宿主本机能写快照即可达
        "stale": False,
        "error": "" if (shared or PEER_ROOT) else "未配置 PEER_SHARED / PEER_ROOT, peer 探测未启用",
        "targets": targets,
        "peers": peers,
        "up_count": sum(1 for p in peers if p["reachable"]),
        "total_count": len(peers),
        # 待办审批(非终结态任务) + 审计尾部。没有配置来源时两者都是空数组,
        # 前端据此显示"未配置"而不是"没有数据"。
        "pending": scan_tasks(state_dirs),
        "audit": scan_audit(state_dirs),
        "state_dirs": state_dirs,
    }

    # 直接覆盖写, 不经过 rename —— 与其它采集脚本(a2a_tasks/worker)一致:
    # 读侧读到半截 JSON 时会当"损坏"降级, 下一轮自然恢复, 比两边策略不同
    # 更好推理。
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.chmod(OUT, 0o600)
    print(f"wrote {OUT}: {payload['up_count']}/{payload['total_count']} peer(s) reachable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
