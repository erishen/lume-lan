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
# 容器侧的 /api/approve 批完之后会写这个标记(见 src/approve_route.lume),
# 表示"状态变了, 请尽快重采"。常驻模式(loop)据此提前跑一轮, 不必干等
# REFRESH_SECS —— 批完立刻能看到结果, 否则用户以为没生效会重复点。
REFRESH_FLAG = os.path.join(ROOT, ".data", ".peers-refresh")
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

    # /peer/agent: 判断这个节点有没有"能问答的 agent"。
    # 为什么必须探测而不是假设: 只有挂了 agent-httpd 的节点(lume-lan 自己)才有;
    # 纯数据节点(博客)没有这个路由, 直接 POST 会拿到 404 的 HTML —— 界面上
    # 表现为"问了一句, 回了一句看不懂的错"。先探测, 界面才能决定给不给输入框。
    ok, a = get_json(shared, node, base, "/peer/agent", timeout)
    entry["agent"] = bool(ok and a.get("ok") and a.get("agent_ok"))

    # /peer/info 是可选端点(只读节点如博客才有); 没有就当没有, 不算错
    ok, i = get_json(shared, node, base, "/peer/info", timeout)
    if ok and i.get("ok"):
        entry["info"] = i
    return entry


AUDIT_TAIL = 200          # 审计只取尾部: 全量可能几 MB, 看板只要最近几条

def _stage_of(c) -> dict:
    """把审批链的一环(可能只存了角色名)规范成 {role,node,stage}。

    兼容两种形态(见 scan_tasks 调用处注释), 保证 API 形状稳定,
    前端与 /api/approve 都只处理一种结构。
    """
    if isinstance(c, dict):
        return {"role": str(c.get("role", "")),
                "node": str(c.get("node", "")),
                "stage": str(c.get("stage", ""))}
    return {"role": str(c), "node": "", "stage": "legacy"}



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
                # 审批链有两种落地形态, 都要认:
                #   新: [{"stage":"first","role":"agent_reviewer","node":"scanner"}, ...]
                #   旧: ["agent_reviewer","agent_reviewer_second","human_admin"]
                # 早期由 approval.chain_for(risk) 按风险分级生成的是纯角色名数组。
                # 只认 dict 会让这批老任务的链整条变成 [] —— 前端于是显示"没有
                # 审批链", 而后端其实正等着人批, 是最容易误判成"无需审批"的坑。
                "policy": [_stage_of(c) for c in chain],
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


# 容器侧审计(看板自己发起的那一跳)。与执行方的 a2a-audit.jsonl 是**两份**:
#   执行方 → 它收到并处理的调用(含 worker 发出的)
#   看板   → 看板自己发出、但可能根本没到达执行方的调用(网络/认证失败)
# 只有合并起来才能回答"这一步到底出没出去": 只有看板侧有 → 没到对端;
# 只有执行方有 → 不是从看板发起的(脚本/CLI); 两边都有 → 完整往返。
UI_AUDIT_DIR = os.path.join(ROOT, "audit")
UI_AUDIT_FILES = ("a2a-ui-audit.jsonl", "a2a-ui-audit.jsonl.1")


def _read_jsonl(path: str, limit: int) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue      # 坏行跳过: 审计文件可能是半截写入
    return out


def scan_audit(state_dirs, limit: int = AUDIT_TAIL) -> list:
    """读审计尾部。JSONL 每行一条; 坏行跳过(审计文件可能是半截写入)。"""
    rows = []
    # 先收容器侧, 再收各执行方; 最后统一按时间排序, 所以顺序不影响结果。
    for fn in UI_AUDIT_FILES:
        for e in _read_jsonl(os.path.join(UI_AUDIT_DIR, fn), limit):
            e["_src"] = "lan-ui"
            rows.append(e)

    for d in state_dirs:
        path = os.path.join(d, "a2a-audit.jsonl")
        if not os.path.isfile(path):
            continue
        src = os.path.basename(os.path.dirname(d))
        for e in _read_jsonl(path, limit):
            e["_src"] = src
            rows.append(e)

    merged = []
    for e in rows:
        src = e.get("_src", "")
        merged.append({
            "at": int(e.get("at") or 0),
            "event": str(e.get("event", "")),
            "task_id": str(e.get("task_id", "")),
            "role": str(e.get("role") or e.get("by_role") or ""),
            "required_role": str(e.get("required_role", "")),
            "claimed_role": str(e.get("claimed_role", "")),
            "decision": str(e.get("decision") or e.get("state") or ""),
            "reason": str(e.get("reason", ""))[:120],
            "source": src,
            # ---- A2A 调用专用(a2a_call 事件才有值) ----
            # 审批事件回答"谁批了什么"; 这些字段回答"发起了什么、多快、
            # 对端怎么答" —— 两类问题分开记, 排障时才不会互相干扰。
            "target": str(e.get("target", "")),
            "method": str(e.get("method", "")),
            "outcome": str(e.get("outcome", "")),
            "status": int(e.get("status") or 0),
            "elapsed_ms": int(e.get("elapsed_ms") or 0),
            "detail": str(e.get("detail", ""))[:120],
            "origin": str(e.get("origin", "")),
            "actor": str(e.get("actor", "")),
            "tool": str(e.get("tool", "")),
        })
    rows = merged
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
    # 消费刷新标记: 这一轮已经是最新的了。留着不删会让每轮都当成"有请求",
    # 变成变相的连续采集(每次都要打一遍所有对端, 没必要)。
    try:
        os.remove(REFRESH_FLAG)
    except OSError:
        pass
    return 0


def loop(interval: float) -> int:
    """常驻模式: 每 interval 秒采一轮; 有刷新标记就立刻采一轮。

    标记由容器侧的审批接口写(它不能自己采 —— 容器在 NAT 网段, 探测必须在宿主
    做, 见文件头)。所以"批完立刻可见"要靠宿主这边主动看一眼这个文件。
    """
    while True:
        try:
            main()
        except Exception as err:      # 采集失败不能让循环退出, 否则之后再也不更新
            print(f"peers_fetch failed: {err}", file=sys.stderr)
        # 分段睡, 好让刷新标记最多 interval 内被发现, 又不必忙等
        waited = 0.0
        step = 1.0
        while waited < interval:
            time.sleep(step)
            waited += step
            if os.path.exists(REFRESH_FLAG):
                break


if __name__ == "__main__":
    if "--loop" in sys.argv:
        try:
            iv = float(os.environ.get("PEER_REFRESH_SECS") or 60)
        except ValueError:
            iv = 60.0
        sys.exit(loop(iv))
    sys.exit(main())
