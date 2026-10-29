#!/usr/bin/env python3
"""a2a_probe.py — 用一个「外部 A2A 客户端」的视角验证 peer 节点。

这个脚本**不复用** peer_bridge.py 的任何代码：它只按 A2A 规范走
`/.well-known/agent-card.json` 发现 + JSON-RPC `message/send` 调用，
用来证明「第三方客户端也能用」这件事不是自说自话。

用法:
    python3 scripts/a2a_probe.py http://192.168.1.10:18191
    python3 scripts/a2a_probe.py http://127.0.0.1:18192 "你能做什么"

检查项(全部通过则 exit 0):
  1. Agent Card 在约定路径可取
  2. name 是字符串、skills 是非空数组（A2A 的最小有效性要求）
  3. supportedInterfaces 里至少有一个 JSONRPC 端点，且 url 可解析
  4. message/send 返回合法 Task（含 id / contextId / status.state）
  5. completed 的 Task 带 artifacts，且 parts 里有 text
  6. 未实现的方法回 -32601，参数缺失回 -32602
  7. 模糊问题回 input-required（而非乱答）
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

TIMEOUT = 20
FAILS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global FAILS
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}" + (f"  ({detail})" if detail else ""))
        FAILS += 1


def get_json(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read(1 << 20) or b"{}")


def rpc(url: str, method: str, params) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": "probe-1",
                       "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read(1 << 20) or b"{}")
    except urllib.error.HTTPError as e:
        return {"http_error": e.code}


def check_stream(url: str, text: str):
    """POST message/stream, 断言 Content-Type 是 SSE 且事件类型合法。
    返回 (ok, note)。"""
    body = json.dumps({
        "jsonrpc": "2.0", "id": "probe-stream", "method": "message/stream",
        "params": {"message": {"kind": "message", "role": "user",
                               "messageId": "m-stream",
                               "parts": [{"kind": "text", "text": text}]}},
    }).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json",
                 "Accept": "text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            ctype = r.headers.get("Content-Type", "")
            raw = r.read(1 << 20).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return False, f"请求失败: {e}"

    if "text/event-stream" not in ctype:
        # 未声明支持的实现会退回 JSON —— 那是"没有流式", 不是错误行为
        return False, f"Content-Type={ctype!r}（未启用流式, 建议 card 里 streaming=false）"

    kinds = []
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            ev = json.loads(line[6:])
        except ValueError:
            continue
        res = ev.get("result") or {}
        kinds.append(res.get("kind", ""))
    if not kinds:
        return False, "SSE 里没有可解析的 data: 事件"
    if "status-update" not in kinds:
        return False, f"事件类型 {kinds} 缺 status-update"
    return True, ",".join(kinds)


def send(url: str, text: str) -> dict:
    return rpc(url, "message/send", {
        "message": {"kind": "message", "role": "user", "messageId": "m-probe",
                    "parts": [{"kind": "text", "text": text}]},
    })


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    base = sys.argv[1].rstrip("/")
    question = sys.argv[2] if len(sys.argv) > 2 else "博客有多少篇文章？"

    print(f"A2A probe → {base}")

    # ---- 1~3 发现 ----
    try:
        card = get_json(base + "/.well-known/agent-card.json")
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ 取不到 Agent Card: {e}")
        return 1

    check("Agent Card 可获取", isinstance(card, dict))
    check("name 是字符串", isinstance(card.get("name"), str))
    check("skills 是非空数组", isinstance(card.get("skills"), list) and len(card["skills"]) > 0,
          f"skills={type(card.get('skills')).__name__}")

    ifaces = card.get("supportedInterfaces") or []
    rpc_url = ""
    for it in ifaces:
        if it.get("protocolBinding") == "JSONRPC" and it.get("url"):
            rpc_url = it["url"]
            break
    if not rpc_url:
        rpc_url = card.get("url", "")   # v0.3 兼容
    check("有 JSONRPC 端点(url)", bool(rpc_url), "supportedInterfaces 与 url 都为空")

    if not rpc_url:
        print(f"\n✗ {FAILS} 项未通过")
        return FAILS

    # ---- 4~5 正常调用 ----
    res = send(rpc_url, question)
    task = res.get("result") or {}
    check("message/send 返回 task", task.get("kind") == "task", str(res)[:120])
    check("task 有 id/contextId", bool(task.get("id")) and bool(task.get("contextId")))
    state = (task.get("status") or {}).get("state", "")
    check("status.state 是已知值",
          state in ("submitted", "working", "completed", "failed",
                    "input-required", "canceled", "rejected"), state)

    if state == "completed":
        arts = task.get("artifacts") or []
        check("completed 带 artifacts", len(arts) > 0)
        text = ""
        if arts:
            for p in arts[0].get("parts", []):
                if p.get("kind") == "text":
                    text = p.get("text", "")
                    break
        check("artifacts 里有 text part", bool(text))
        if text:
            print(f"    回答: {text[:100]}")

    # ---- 6 错误码 ----
    # 用真正未实现的方法探测(-32601 是"方法不存在"), 不要用 message/stream ——
    # 它已经实现了, 拿它探会把"参数错误 -32602"误判成"未实现"。
    e1 = rpc(rpc_url, "tasks/resubscribe", {})
    check("未实现方法回 -32601",
          (e1.get("error") or {}).get("code") == -32601, str(e1)[:100])

    e2 = rpc(rpc_url, "message/send", {"message": {"parts": []}})
    check("空 parts 回 -32602",
          (e2.get("error") or {}).get("code") == -32602, str(e2)[:100])

    # ---- 7 边界行为 ----
    # 两类节点这里**应该不一样**, 不能一刀切:
    #   - 固定查询节点(博客): 认不出意图就该回 input-required, 而不是猜一个工具
    #   - agent 节点(lan): 背后是 LLM, 模糊问题它本来就能答, completed 才对
    # 判据: card.capabilities 里的自定义扩展字段(规范允许扩展), 由节点自报。
    # 靠 skill 名字猜不可靠(agent 节点的 skill 也可能叫 fs__read_file),
    # 所以让节点自己在 card 里声明 capabilities.agent_backed。
    is_conversational = bool((card.get("capabilities") or {}).get("agent_backed"))
    vague = send(rpc_url, "今天天气怎么样")
    vtask = vague.get("result") or {}
    vstate = (vtask.get("status") or {}).get("state", "")
    if is_conversational:
        check("模糊问题由 agent 正常处理(completed/input-required 均可)",
              vstate in ("completed", "input-required"), f"state={vstate}")
    else:
        check("固定查询节点对模糊问题回 input-required 而不是乱猜",
              vstate in ("input-required", "rejected"), f"state={vstate}")

    # ---- 8 长任务模型 ----
    # 提交异步任务 -> 拿 taskId -> tasks/get 查得到 -> 未知 id 回 -32001
    async_res = rpc(rpc_url, "message/send", {
        "message": {"kind": "message", "role": "user", "messageId": "m-async",
                    "parts": [{"kind": "text", "text": question}]},
        "configuration": {"async": True},
    })
    atask = async_res.get("result") or {}
    astate = (atask.get("status") or {}).get("state", "")
    check("异步提交返回 submitted", astate == "submitted", f"state={astate}")
    tid = atask.get("id", "")

    if tid:
        got = rpc(rpc_url, "tasks/get", {"id": tid})
        gt = got.get("result") or {}
        check("tasks/get 能查到该任务", gt.get("id") == tid, str(got)[:100])

        # 已终结任务不可取消（刚提交的是 submitted，可取消）
        cancel = rpc(rpc_url, "tasks/cancel", {"id": tid})
        cstate = ((cancel.get("result") or {}).get("status") or {}).get("state", "")
        check("tasks/cancel 能取消未终结任务", cstate == "canceled", str(cancel)[:100])

        # 再取消一次应回 -32002（已终结）
        again = rpc(rpc_url, "tasks/cancel", {"id": tid})
        check("重复取消回 -32002",
              (again.get("error") or {}).get("code") == -32002, str(again)[:100])

    nf = rpc(rpc_url, "tasks/get", {"id": "definitely-not-exist"})
    check("查不存在的任务回 -32001",
          (nf.get("error") or {}).get("code") == -32001, str(nf)[:100])

    # ---- 9 多轮续发(input-required 回路) ----
    # 步骤: 提需审批的异步任务 -> 轮询应停在 input-required 且带 status.message
    #       -> 带同 taskId 回 yes -> 应回到 submitted -> 对已完成任务续发回 -32004
    appr = rpc(rpc_url, "message/send", {
        "message": {"kind": "message", "role": "user", "messageId": "m-appr",
                    "parts": [{"kind": "text", "text": question}]},
        "configuration": {"async": True},
        "payload": {"kind": "peer_invoke", "target": base.split("//")[-1].rstrip("/"),
                    "tool": "blog.stats" if not is_conversational else "echo__pong",
                    "arguments": {}, "require_approval": True,
                    "approve_prompt": "确认执行请回复 yes"},
    })
    at = appr.get("result") or {}
    atid = at.get("id", "")
    check("审批任务已提交", at.get("status", {}).get("state") == "submitted", str(appr)[:100])

    # 让 worker 跑一轮：本探针不直接调 worker（它可能在另一个进程），
    # 所以这里只验证「提交后能查到、状态合法」，input-required 的到达
    # 由 worker 侧测试覆盖（见 docs/peer.md 的实测记录）。
    if atid:
        g = rpc(rpc_url, "tasks/get", {"id": atid})
        gs = ((g.get("result") or {}).get("status") or {}).get("state", "")
        check("审批任务可查询且状态合法",
              gs in ("submitted", "working", "input-required", "completed"), gs)

    # 对不存在任务续发 -> -32001
    nf2 = rpc(rpc_url, "message/send", {
        "id": "no-such-task-xyz",
        "message": {"kind": "message", "role": "user", "messageId": "m-x",
                    "parts": [{"kind": "text", "text": "x"}]},
    })
    check("对不存在任务续发回 -32001",
          (nf2.get("error") or {}).get("code") == -32001, str(nf2)[:100])

    # ---- 10 流式 message/stream ----
    # 只验证"服务端确实以 text/event-stream 回, 且事件序列符合规范" ——
    # 具体执行是否真在推进不在探针职责内(见 docs 的"同步回放"说明)。
    stream_ok, stream_note = check_stream(rpc_url, question)
    check("message/stream 返回 SSE 事件流", stream_ok, stream_note)

    if FAILS == 0:
        print("✓ 全部通过")
    else:
        print(f"✗ {FAILS} 项未通过")
    return FAILS


if __name__ == "__main__":
    sys.exit(main())
