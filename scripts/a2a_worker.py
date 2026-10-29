#!/usr/bin/env python3
"""a2a_worker.py — A2A 长任务的执行者(宿主侧)

为什么需要单独的进程
------------------
lume 没有 spawn/exec，`http_*` 又是**收完整响应才返回**的同步调用。所以
「提交后立刻返回 taskId、后台慢慢跑」这件事，在纯 lume 里做不到 —— 必须
有个外部进程把任务从 submitted 推到 completed。这就是它。

它做什么
-------
循环扫 `.data/a2a-tasks/*.json`：
  1. 状态是 `submitted` 的 → 置 `working`，按 metadata.payload 派活
  2. 执行完 → 置 `completed`（带 artifacts）或 `failed`（带原因）
  3. 己终结且超过 TTL 的 → 删除（lume 没有 unlink，只能在这里删）

派活方式
-------
payload 形如 `{"kind":"peer_invoke","target":"127.0.0.1:18182",
"tool":"pse-review__pse-review","arguments":{...}}`

worker 用与 peer_bridge 完全相同的签名规则去调对端的 `/peer/invoke`。
「任务」和「工具调用」在这里是同一件事的不同外壳：A2A 的 Task 提供
生命周期与可查询性，实际执行仍是 peer 协议。

用法
----
    python3 scripts/a2a_worker.py            # 常驻，每 2 秒扫一次
    python3 scripts/a2a_worker.py --once     # 只跑一轮（调试/CI）

生产建议挂 launchd / systemd，与 refresh-once.sh 平级。
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
TASK_DIR = os.path.join(ROOT, ".data", "a2a-tasks")
ENV = os.path.join(ROOT, ".env")

POLL_SECS = 2
TTL_SECS = 86400
# 单任务最长执行时间。pse-review 实测可达 10 分钟，留足余量。
TASK_MAX_SECS = 1800


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
    for k in ("PEER_SHARED", "PEER_NAME", "PEER_ROOT",
              "A2A_TASK_TTL_SECS", "A2A_TASK_MAX_SECS"):
        if os.environ.get(k):
            vals[k] = os.environ[k]
    return vals


ENV_VALS = load_env()
SHARED = (ENV_VALS.get("PEER_SHARED") or "").strip()
NODE = (ENV_VALS.get("PEER_NAME") or "a2a-worker").strip()
# 身份可信层: 配了 PEER_ROOT 就用「本节点派生密钥」签名, 与
# lume-core/lib/identity.lume 的 key_for() 逐字节一致。
PEER_ROOT = (ENV_VALS.get("PEER_ROOT") or "").strip()
TTL_SECS = int(ENV_VALS.get("A2A_TASK_TTL_SECS") or TTL_SECS)
TASK_MAX_SECS = int(ENV_VALS.get("A2A_TASK_MAX_SECS") or TASK_MAX_SECS)


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ---- peer 签名（与 lume-core/lib/peer.lume 逐字节一致） ----------------

def key_for(node: str) -> str:
    """节点派生密钥: HMAC-SHA256(PEER_ROOT, "peer:" + node)。
    与 lume-core/lib/identity.lume 的 key_for() 必须一致 —— 由
    tests/test_identity.py::test_python_mirror_matches_lume 钉住。"""
    return hmac.new(PEER_ROOT.encode(), f"peer:{node}".encode(),
                    hashlib.sha256).hexdigest()


def _sig(ts: str, nonce: str, scope: str) -> str:
    msg = "\n".join([NODE, ts, nonce, scope]).encode()
    # 配了 PEER_ROOT -> 用自己的派生密钥; 否则回退共享密钥(向后兼容)
    key = key_for(NODE) if PEER_ROOT else SHARED
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


def peer_call(target: str, path: str, body: str) -> tuple:
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    scope = f"POST {path}\n{body}"
    q = f"node={NODE}&ts={ts}&nonce={nonce}&sig={_sig(ts, nonce, scope)}"
    req = urllib.request.Request(
        f"http://{target}{path}?{q}", data=body.encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TASK_MAX_SECS) as r:
            return True, json.loads(r.read(1 << 20) or b"{}")
    except urllib.error.HTTPError as e:
        return False, {"error": f"HTTP {e.code}"}
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return False, {"error": str(getattr(e, "reason", e))}


# ---- 任务文件读写（与 a2a_tasks.lume 相同的布局） ----------------------

def task_files() -> list:
    try:
        return [f for f in os.listdir(TASK_DIR) if f.endswith(".json")]
    except FileNotFoundError:
        return []


def read_task(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_task(path: str, task: dict) -> None:
    # 直接覆盖写，不经过 rename —— 与 lume 侧 a2a_tasks.lume 的取舍一致:
    # lume 没有 rename 内建, 两边行为要对齐, 否则一半原子一半不原子更难推理。
    # 读到半截 JSON 时 read_task() 返回 None, 下一轮轮询自然恢复。
    with open(path, "w", encoding="utf-8") as f:
        json.dump(task, f, ensure_ascii=False, indent=2)


def transition(path: str, task: dict, state: str, artifacts,
               extra=None, msg_text: str = "") -> None:
    """状态流转。msg_text 非空时写进 status.message —— 
    A2A 用它告诉客户端"我需要什么"(input-required 时尤其重要)。
    恢复时传空串会把上一条提问清掉，避免客户端读到过期的疑问。"""
    st = task.setdefault("status", {})
    st["state"] = state
    st["timestamp"] = str(int(time.time()))
    if msg_text:
        st["message"] = {"role": "agent",
                         "parts": [{"kind": "text", "text": msg_text}]}
    else:
        st.pop("message", None)
    task["artifacts"] = artifacts
    if extra:
        task.update(extra)
    write_task(path, task)


# ---- 审计 -------------------------------------------------------------
# 形状照 knowledge/shared/abac.lume 的 audit_write: JSONL 追加, 每行一条,
# `at` 是 unix 秒。它是 peer 审批唯一的「谁在什么时候批了什么」的证据 ——
# 任务文件的 history 只记对话, 不记决策。
AUDIT_PATH = os.path.join(ROOT, ".data", "a2a-audit.jsonl")


def audit(entry: dict) -> None:
    e = dict(entry)
    e.setdefault("at", int(time.time()))
    try:
        os.makedirs(os.path.dirname(AUDIT_PATH), exist_ok=True)
        with open(AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    except OSError as err:
        # 审计写不进去不能阻断执行(与 abac 同样取舍), 但要在 stderr 里可见
        log(f"audit write failed: {err}")


def payload_digest(payload: dict) -> str:
    """把 payload 归一化后取摘要 —— 审计里记摘要而不是全文:
    一来体积可控, 二来能证明「批的就是跑的这个」。"""
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def artifact(text: str) -> dict:
    return {
        "artifactId": secrets.token_hex(12),
        "parts": [{"kind": "text", "text": text}],
    }


# ---- 执行 --------------------------------------------------------------

# 审批判定：**精确匹配**，不是包含匹配。
#
# 原实现是 `any(w in text)`，于是「不 yes 吧」「好像可以」「是它吗」都会被判成
# 批准 —— 只读场景代价小(重跑一次)，但一旦接写操作(发文章/删数据)就成了真实
# 的授权漏洞，而且比没有审批更危险: 它给出「已确认」的错觉。
#
# 现在只认归一化后**完全等于**下列某个词的回答。多一个字都不批 —— 宁可多问
# 一轮，也不能误批。
APPROVAL_EXACT = {"yes", "y", "ok", "true", "确认", "同意", "批准", "approved"}

# 归一化: 去首尾空白、折叠内部空白、去尾部感叹号/句号、转小写。
def _norm(text: str) -> str:
    t = text.strip().lower()
    t = " ".join(t.split())
    return t.rstrip("!！。.~～")


def last_user_text(task: dict) -> str:
    for m in reversed(task.get("history") or []):
        if m.get("role") == "user":
            parts = m.get("parts") or []
            if parts:
                return str(parts[0].get("text", ""))
    return ""


def is_approved(text: str) -> bool:
    return _norm(text) in APPROVAL_EXACT


# ---- 审批链 -----------------------------------------------------------
# 与 lume-core/lib/approval.lume **逐条对应**的 Python 镜像。
# 为什么要有两份: 状态机在 lume 侧(A2A 端点), 执行在宿主 worker(Python),
# 两边都要判"这一关该谁、跨角色是否越权"。规则必须完全一致, 否则 lume 说
# 轮到你、worker 说没轮到 —— 那种分歧极难排查。改一处必须改两处,
# 由 tests/test_approval_chain.py 的三方一致性用例钉住。
ROLE_ACTIONS = {
    "agent_operator":        ["execute", "request"],
    "agent_reviewer":        ["review_first"],
    "agent_reviewer_second": ["review_second"],
    "human_admin":           ["approve", "override", "audit"],
}
CHAIN_STANDARD = ["agent_reviewer", "agent_reviewer_second", "human_admin"]
CHAIN_SINGLE = ["human_admin"]
CHAIN_PEER = ["agent_reviewer"]


# ---- 领域 → 审批链（与 lume-core/lib/domains.lume 逐条对应） ----
# 二审刻意取**另一个领域**的节点：同领域两人容易同源偏差，等于没复核。
DOMAIN_REGISTRY = os.path.join(ROOT, ".data", "domains.json")


def _fallback_chain() -> list:
    return [{"stage": "final", "role": "human_admin", "node": "",
             "why": "未登记领域或登记不完整, 人工终审"}]


def load_domains() -> dict:
    try:
        with open(DOMAIN_REGISTRY, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def chain_for_domain(domain: str) -> list:
    """按领域取审批链。未登记/登记错 -> 兜底到 human_admin，绝不无人审。"""
    reg = load_domains()
    d = (reg.get("domains") or {}).get(domain)
    if not isinstance(d, dict):
        return _fallback_chain()
    first, second = d.get("first"), d.get("second")
    out = []
    if isinstance(first, dict):
        out.append({"stage": "first", "role": "agent_reviewer",
                    "node": str(first.get("node", "")),
                    "why": str(first.get("why", ""))})
    if isinstance(second, dict):
        out.append({"stage": "second", "role": "agent_reviewer_second",
                    "node": str(second.get("node", "")),
                    "why": str(second.get("why", ""))})
    # 只配了 second 没配 first: "没有一审就二审"语义不成立 -> 兜底
    if not out or out[0]["stage"] != "first":
        return _fallback_chain()
    out.append({"stage": "final", "role": "human_admin", "node": "",
                "why": "人工终审"})
    return out


def approval_chain(risk: str = "medium") -> list:
    if risk == "peer":
        return list(CHAIN_PEER)
    if risk == "low":
        return list(CHAIN_SINGLE)
    # medium / high / 未知 一律最严 —— 未知风险不能当低风险放行
    return list(CHAIN_STANDARD)


def chain_role(stage) -> str:
    """链上某一关的角色名。
    兼容两种链形状: 领域链是 map 数组({stage,role,node,why}),
    按风险分级的旧链是字符串数组。统一走这个函数, 免得调用方到处判断。
    这个 bug 真的发生过: 领域链接进来后 approval_next_role 仍按字符串遍历,
    于是把整个 stage 对象当角色名返回, 提示里打印出一大段 JSON。"""
    if isinstance(stage, dict):
        return str(stage.get("role", ""))
    return str(stage)


def approval_next_role(chain: list, approvals: list) -> str:
    """下一关该谁。返回 "" 表示链已走完(或已被驳回)。"""
    for stage in chain:
        need = chain_role(stage)
        if not need:
            continue
        mine = [a for a in approvals if a.get("role") == need]
        if any(a.get("decision") == "reject" for a in mine):
            return ""
        if not any(a.get("decision") == "approve" for a in mine):
            return need
    return ""


def approval_is_complete(chain: list, approvals: list) -> bool:
    if approval_next_role(chain, approvals):
        return False
    return not any(a.get("decision") == "reject" for a in approvals)


def approval_try(chain: list, approvals: list, role: str, actor: str,
                 decision: str, note: str) -> dict:
    """尝试用某角色批一关。返回 {ok, error, approvals, done, next_role, rejected}"""
    if role not in ROLE_ACTIONS:
        return {"ok": False, "error": f"unknown role: {role}", "approvals": approvals,
                "done": False, "next_role": "", "rejected": False}
    if decision not in ("approve", "reject"):
        return {"ok": False, "error": "decision must be approve|reject",
                "approvals": approvals, "done": False, "next_role": "", "rejected": False}

    need = approval_next_role(chain, approvals)
    if not need:
        rejected = any(a.get("decision") == "reject" for a in approvals)
        return {"ok": False, "error": "chain already finished", "approvals": approvals,
                "done": True, "next_role": "", "rejected": rejected}
    if need != role:
        return {"ok": False, "error": f"not your turn (need {need}, got {role})",
                "approvals": approvals, "done": False, "next_role": need, "rejected": False}

    nxt = list(approvals) + [{"role": role, "actor": actor, "decision": decision,
                              "at": int(time.time()), "note": note}]
    rejected = any(a.get("decision") == "reject" for a in nxt)
    after = approval_next_role(chain, nxt)
    return {"ok": True, "error": "", "approvals": nxt,
            "done": after == "" , "next_role": after, "rejected": rejected}


def parse_verdict(text: str) -> dict:
    """解析客户端回答为审批指令。

    约定: `<role>: approve` / `<role>: reject <理由>`; 纯 `yes` 视为用
    **当前待审角色**批(role 留空, 由调用方补 need)。认不出就 decision=""。
    这样既让人能直接回 yes, 又允许明确指定身份。
    """
    t = text.strip()
    if not t:
        return {"role": "", "decision": ""}
    low = t.lower()
    # 带角色前缀
    if ":" in t:
        head, _, tail = t.partition(":")
        r = head.strip()
        body = _norm(tail)
        if r in ROLE_ACTIONS:
            if body in ("approve", "yes", "ok", "确认", "批准"):
                return {"role": r, "decision": "approve"}
            if body.startswith("reject") or body in ("no", "拒绝", "驳回"):
                return {"role": r, "decision": "reject"}
            return {"role": r, "decision": ""}
    # 裸回答
    if _norm(t) in APPROVAL_EXACT:
        return {"role": "", "decision": "approve"}
    if _norm(t) in ("no", "reject", "拒绝", "驳回"):
        return {"role": "", "decision": "reject"}
    return {"role": "", "decision": ""}


def run_task(path: str, task: dict) -> None:
    tid = task.get("id", "?")
    payload = (task.get("metadata") or {}).get("payload") or {}
    kind = payload.get("kind", "")

    # ---- 审批链 ----
    # 多级串行: agent_reviewer -> agent_reviewer_second -> human_admin(按风险)
    # 链的定义与"谁该审哪一关"在 lume-core/lib/approval.lume —— 那是角色与
    # 动作的**互斥表**(职责分离), 不是等级表: human_admin 能 approve 但
    # 不能 execute, 终审者不得绕过流程直接执行。
    #
    # 这里的职责只有两件: 读状态、问引擎"这一关该谁", 然后把审批记录落盘。
    if payload.get("require_approval") and not payload.get("_approved"):
        answer = last_user_text(task)
        approvals = payload.get("_approvals") or []
        chain = payload.get("_chain")
        if not chain:
            # 优先按**领域**排链(谁懂这行谁一审, 另一个领域二审);
            # 没声明 domain 时才退回按风险分级。
            domain = payload.get("domain", "")
            if domain:
                chain = chain_for_domain(domain)
                if len(chain) == 1 and chain[0]["stage"] == "final":
                    log(f"task {tid}: 领域 '{domain}' 未登记, 回退人工终审")
            else:
                chain = approval_chain(payload.get("risk", "medium"))
            payload["_chain"] = chain

        # 这一关该谁审
        need = approval_next_role(chain, approvals)
        # 这一关该谁(node) + 为什么是它(why)。提示里要讲清找谁, 否则人不知道
        # 该把这句 "xx: approve" 发给哪个节点。
        need_node, need_why = "", ""
        for st in chain:
            if chain_role(st) == need and isinstance(st, dict):
                need_node, need_why = st.get("node", ""), st.get("why", "")
                break
        who = f"{need}（{need_node}）" if need_node else need
        actor = payload.get("_actor") or ""          # 谁在批(节点名)
        role_of_actor = payload.get("_actor_role") or ""

        # 客户端这一轮的回答是不是"用以审批"的?
        # 约定: 回答以 role 名开头表示以该角色批, 例如 "agent_reviewer: approve"。
        # 纯 "yes" 视为用当前待审角色批(便于人工/老客户端), 但必须角色对得上。
        parsed = parse_verdict(answer)
        verdict = parsed["decision"]
        claim_role = parsed["role"] or need

        if verdict == "":
            # 不是审批指令 -> 继续等, 并把"该谁审"讲清楚
            what = payload.get("approve_prompt") or (
                f"即将执行 {payload.get('tool', '?')}，当前需 {who} 审核。"
                + (f"（{need_why}）" if need_why else "")
                + f"回复「{need}: approve」或「{need}: reject <理由>」。")
            transition(path, task, "input-required", [], msg_text=what)
            audit({"event": "approval_required", "task_id": tid,
                   "tool": payload.get("tool", ""), "target": payload.get("target", ""),
                   "required_role": need, "answer": answer[:80], "decision": "PENDING"})
            log(f"task {tid}: input-required (等待 {need})")
            return

        # 走引擎判定: 这个角色能不能做这个动作, 且是不是轮到他
        res = approval_try(chain, approvals, claim_role, actor, verdict, answer[:200])
        if not res["ok"]:
            # 越权/抢批 -> 驳回这次尝试, 任务继续停在原关
            transition(path, task, "input-required", [], msg_text=(
                f"审批被拒: {res['error']}。当前需 {who} 审核。"))
            audit({"event": "approval_denied", "task_id": tid,
                   "required_role": need, "claimed_role": claim_role,
                   "answer": answer[:80], "decision": "DENY",
                   "reason": res["error"]})
            log(f"task {tid}: approval denied ({res['error']})")
            return

        payload["_approvals"] = res["approvals"]
        task.setdefault("metadata", {})["payload"] = payload
        audit({"event": "approval_step", "task_id": tid,
               "role": claim_role, "actor": actor, "decision": verdict.upper(),
               "next_role": res["next_role"], "chain_done": res["done"]})

        if res["rejected"]:
            transition(path, task, "rejected", [],
                       msg_text=f"审批链在 {claim_role} 处驳回: {answer[:120]}")
            audit({"event": "task_finished", "task_id": tid, "state": "rejected",
                   "by_role": claim_role})
            log(f"task {tid}: rejected by {claim_role}")
            return

        if not res["done"]:
            nxt = res["next_role"]
            transition(path, task, "input-required", [],
                       msg_text=f"{claim_role} 已通过，现需 {nxt} 审核。")
            log(f"task {tid}: {claim_role} approved -> next {nxt}")
            return

        # 全链通过
        payload["_approved"] = True
        task.setdefault("metadata", {})["payload"] = payload
        audit({"event": "approval_granted", "task_id": tid,
               "tool": payload.get("tool", ""), "target": payload.get("target", ""),
               "chain": chain, "decision": "ALLOW"})
        log(f"task {tid}: approval chain complete")

    transition(path, task, "working", [])
    log(f"task {tid}: working (kind={kind})")

    if kind == "peer_invoke":
        target = payload.get("target", "")
        tool = payload.get("tool", "")
        args = payload.get("arguments") or {}
        if not target or not tool:
            transition(path, task, "failed",
                       [artifact("任务参数不完整: 需要 target 与 tool")])
            log(f"task {tid}: failed (bad payload)")
            return
        body = json.dumps({"tool": tool, "arguments": args}, ensure_ascii=False)
        ok, res = peer_call(target, "/peer/invoke", body)
        if not ok:
            transition(path, task, "failed",
                       [artifact(f"对端不可达: {res.get('error', '')}")])
            log(f"task {tid}: failed (unreachable {target})")
            return
        if not res.get("ok"):
            transition(path, task, "failed",
                       [artifact(f"执行失败: {res.get('error', '')}")])
            log(f"task {tid}: failed ({res.get('error', '')[:80]})")
            return
        result = res.get("result", res)
        text = result if isinstance(result, str) else json.dumps(
            result, ensure_ascii=False, indent=2)
        transition(path, task, "completed", [artifact(text)])
        audit({"event": "task_finished", "task_id": tid, "state": "completed",
               "tool": tool, "target": target, "result_len": len(text),
               "payload_digest": payload_digest(payload)})
        log(f"task {tid}: completed ({len(text)} chars)")
        return

    if kind == "dispatch" and payload.get("question"):
        # 把任务转给某个节点的 agent(A2A 到 A2A 的一跳)
        target = payload.get("target", "")
        if not target:
            transition(path, task, "failed", [artifact("缺少 target")])
            return
        body = json.dumps({
            "jsonrpc": "2.0", "id": "w1", "method": "message/send",
            "params": {"message": {"kind": "message", "role": "user",
                                   "messageId": secrets.token_hex(8),
                                   "parts": [{"kind": "text",
                                              "text": payload["question"]}]}},
        }, ensure_ascii=False)
        ts = str(int(time.time()))
        nonce = secrets.token_hex(16)
        # /a2a 不走签名(公开端点), 直接 POST
        req = urllib.request.Request(f"http://{target}/a2a", data=body.encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TASK_MAX_SECS) as r:
                resp = json.loads(r.read(1 << 20) or b"{}")
        except Exception as e:  # noqa: BLE001
            transition(path, task, "failed", [artifact(f"转发失败: {e}")])
            return
        t2 = resp.get("result") or {}
        arts = t2.get("artifacts") or []
        state2 = (t2.get("status") or {}).get("state", "")
        if state2 == "completed" and arts:
            parts = arts[0].get("parts") or []
            text = next((p.get("text", "") for p in parts
                         if p.get("kind") == "text"), "")
            transition(path, task, "completed", [artifact(text)])
            audit({"event": "task_finished", "task_id": tid, "state": "completed",
                   "tool": "dispatch", "target": target,
                   "result_len": len(text), "payload_digest": payload_digest(payload)})
        else:
            transition(path, task, "failed",
                       [artifact(f"对端状态 {state2}: {json.dumps(t2, ensure_ascii=False)[:200]}")])
        log(f"task {tid}: completed via dispatch")
        return

    transition(path, task, "failed",
               [artifact(f"不支持的 payload.kind: {kind!r}")])
    log(f"task {tid}: failed (unsupported kind {kind!r})")


def gc() -> None:
    now = time.time()
    removed = 0
    for fn in task_files():
        path = os.path.join(TASK_DIR, fn)
        t = read_task(path)
        if not t:
            continue
        st = t.get("status") or {}
        state = st.get("state", "")
        created = int(st.get("created_at") or 0)
        if state in ("completed", "failed", "canceled", "rejected") \
                and created and (now - created) > TTL_SECS:
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    if removed:
        log(f"gc: removed {removed} expired task(s)")


def tick() -> int:
    """扫一轮，返回本轮处理的任务数。"""
    n = 0
    for fn in task_files():
        path = os.path.join(TASK_DIR, fn)
        t = read_task(path)
        if not t:
            continue
        state = (t.get("status") or {}).get("state", "")
        if state != "submitted":
            continue
        # 认领: 先改 working 再干。两个 worker 同时抢时，后到的看到
        # working 就跳过（文件锁在 lume 侧写，这里读-改-写窗口很短）。
        run_task(path, t)
        n += 1
    gc()
    return n


def main() -> int:
    os.makedirs(TASK_DIR, exist_ok=True)
    if "--once" in sys.argv:
        n = tick()
        print(f"processed {n} task(s)")
        return 0
    log(f"a2a worker started (dir={TASK_DIR}, poll={POLL_SECS}s, ttl={TTL_SECS}s)")
    while True:
        try:
            tick()
        except Exception as e:  # noqa: BLE001 — 一轮失败不能让 worker 退出
            log(f"tick error: {type(e).__name__}: {e}")
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    sys.exit(main())
