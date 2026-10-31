#!/usr/bin/env python3
"""peer_bridge.py — stdio MCP server，把局域网内所有 lume peer 节点的能力
暴露给 agent。

为什么需要它
------------
agent-httpd 的 MCP 客户端只支持 **stdio** transport（src/agent/mcp.c:119
明写 "only stdio transport today"），而 peer 节点的能力在 HTTP 后面。
这个桥就是那层适配：它以 stdio 子进程的身份被 agent-httpd 拉起，收到
tools/list / tools/call 后转发给各 peer 的 /peer/tools 与 /peer/invoke。

给 agent 带来的能力
------------------
agent 不再需要预先知道「局域网里有什么」，它每轮都能看到一张聚合后的
工具表：

    blog__blog.stats     文章/标签/浏览量等只读统计
    blog__blog.recent    最近发布的文章标题列表
    lan__agent           对方 agent 是否就绪

agent 自己决定调哪个、传给谁 —— 这就是「发现」的意义：新增一个 lume-*
应用只需要它自己起 peer_node.lume，本桥不用改一行代码。

配置（环境变量，由 agent-httpd 的 MCP 子进程继承）
------------------------------------------------
    PEER_SHARED          与各对端共享的密钥；为空则只暴露发现类工具
    PEER_TARGETS         "127.0.0.1:18171,127.0.0.1:18088"
    PEER_TIMEOUT         单次调用超时秒数(默认 15)

用法（.data/mcp-servers.json 里加一项）
-------------------------------------
    {
      "id": "peers",
      "transport": "stdio",
      "command": "python3",
      "args": "scripts/peer_bridge.py",
      "approval": false
    }

工具命名
-------
    <node>__<tool>      例: blog__blog.stats
node 取自对端的 PEER_NAME（拿不到就用 target 的 host:port）。前缀保证了
不同节点同名工具不会互撞 —— 与 agent-httpd 自己的 <id>:<tool> 同一思想。

零第三方依赖（urllib / hmac / hashlib / json）。
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

TIMEOUT = float(os.environ.get("PEER_TIMEOUT") or 15)
SHARED = (os.environ.get("PEER_SHARED") or "").strip()
NODE = (os.environ.get("PEER_NAME") or "agent-httpd").strip()
# 身份可信层: 配了 PEER_ROOT 就用本节点派生密钥签名(与 lume-core/lib/identity.lume 一致)
PEER_ROOT = (os.environ.get("PEER_ROOT") or "").strip()
TARGETS = [t.strip() for t in (os.environ.get("PEER_TARGETS") or "").split(",") if t.strip()]

# ---- 工具暴露策略 ----------------------------------------------------
# 默认**只暴露只读工具**。为什么不能全量暴露: peer 协议里 /peer/invoke 没有
# 工具白名单(lume-lan 侧), 一旦把对端所有工具挂给 agent, agent 就能跨节点
# 执行 fs__write_file / memory__delete_entities 这类写操作 —— 这把"发现能力"
# 变成了"远程任意执行", 风险远超收益。
#
# PEER_EXPOSE=all           暴露全部(明确的风险自担; 建议只在隔离网络用)
# PEER_EXPOSE=readonly      默认: 按下面 DENY 名单过滤
# PEER_EXPOSE=key1,key2     白名单: 只暴露名字里含任一关键字的工具
EXPOSE = (os.environ.get("PEER_EXPOSE") or "readonly").strip().lower()
# 委派最大跳数。0 或不合法 -> 2。
# 与对端(peer_node.lume 的 PEER_MAX_HOPS)保持同一个默认值: 两边不一致时,
# 由更严的那侧生效(对端会先拒), 但显示给 agent 的"N/M"会误导 —— 所以对齐。
try:
    MAX_HOPS = int(os.environ.get("PEER_MAX_HOPS") or 2)
except ValueError:
    MAX_HOPS = 2
if MAX_HOPS < 1:
    MAX_HOPS = 1

# 写/破坏性动作的命名特征。宁可多拦: 漏掉一个写工具的代价远大于多拦一个读工具。
DENY_TOKENS = (
    "write", "edit", "create", "delete", "remove", "move", "rename",
    "put", "patch", "update", "insert", "drop", "truncate", "mkdir",
    "add_", "set_", "append", "save", "upload", "exec", "run_", "shutdown",
)


def tool_allowed(tool_name: str, peer_callable=None) -> bool:
    """客户端侧的工具可见性。

    peer_callable 来自对端 /peer/tools 的标注（服务端策略的结论）。有它就用它
    —— 服务端才是权威：它知道自己配了哪些 MCP、哪些被策略拦下。客户端再维护
    一份 DENY_TOKENS 只会和服务端漂移，漂移的方向通常是"客户端以为能调、
    服务端拒绝"，表现为 agent 反复试一个永远失败的工具。

    没有该字段时（旧版对端）退回本地词表，保持兼容。
    """
    if peer_callable is False:
        return False
    if EXPOSE == "all":
        return True
    low = tool_name.lower()
    if EXPOSE in ("readonly", "ro", ""):
        return not any(tok in low for tok in DENY_TOKENS)
    # 白名单模式: 逗号分隔的关键字
    return any(k and k in low for k in EXPOSE.split(","))


def send(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# ---- peer 签名（与 lume-core/lib/peer.lume 逐字节一致） ----------------

def key_for(node: str) -> str:
    """节点派生密钥: HMAC-SHA256(PEER_ROOT, "peer:" + node)。"""
    return hmac.new(PEER_ROOT.encode(), f"peer:{node}".encode(),
                    hashlib.sha256).hexdigest()


def _sig(ts: str, nonce: str, scope: str) -> str:
    msg = "\n".join([NODE, ts, nonce, scope]).encode()
    key = key_for(NODE) if PEER_ROOT else SHARED
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


def _signed(base: str, path: str, body: str = "") -> str:
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    method = "POST" if body else "GET"
    scope = f"{method} {path}"
    if body:
        scope += "\n" + body
    q = f"node={NODE}&ts={ts}&nonce={nonce}&sig={_sig(ts, nonce, scope)}"
    return f"{base}{path}?{q}"


def call(base: str, path: str, body: str = ""):
    """返回 (ok, payload)。任何失败都折成 (False, {"error": ...})，
    绝不让异常穿过 —— 一个节点挂了不能拖垮整个 MCP 会话。"""
    url = _signed(base, path, body)
    data = body.encode() if body else None
    headers = {"Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read(1 << 20)
    except urllib.error.HTTPError as e:
        return False, {"error": f"HTTP {e.code}"}
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return False, {"error": str(getattr(e, "reason", e))}
    try:
        return True, json.loads(raw or b"{}")
    except ValueError:
        return False, {"error": "响应不是合法 JSON"}


def discover_apps() -> list:
    """按**应用**聚合(能力层): [{node, site, summary, target, tools:[...]}]。
    发现工具用它生成「局域网里有哪些应用」的一次性答案。"""
    apps = []
    filtered_off.clear()
    for target in TARGETS:
        if not SHARED:
            continue
        ok, payload = call("http://" + target, "/peer/tools")
        if not ok or not payload.get("ok"):
            apps.append({
                "node": target, "site": "", "target": target,
                "summary": "", "reachable": False,
                "error": str(payload.get("error", "不可达")),
                "tools": [],
            })
            continue
        node = str(payload.get("node") or target).strip() or target
        kept = []
        for t in payload.get("tools", []):
            name = str(t.get("name", "")).strip()
            if not name:
                continue
            if not tool_allowed(name, t.get("peer_callable")):
                filtered_off.append(f"{node}__{name}")
                continue
            kept.append({
                "name": f"{node}__{name}",
                "raw": name,
                "desc": str(t.get("desc", "")).strip(),
                "when": str(t.get("when_to_use", "")).strip(),
            })
        apps.append({
            "node": node,
            "site": str(payload.get("site", "")).strip(),
            "summary": str(payload.get("summary", "")).strip(),
            "target": target,
            "reachable": True,
            "error": "",
            "tools": kept,
        })
    return apps


def probe_agents() -> dict:
    """哪些节点背后有能问答的 agent。返回 {node: {target, site}}。

    为什么要先探 /peer/agent 而不是直接给每个节点都挂一个 ask 工具:
      * 纯数据节点(博客)没有这条路由, 给了工具 agent 会去调, 拿到 404 ——
        agent 会反复重试或换着问, 白白烧几轮(token 与延迟都是成本);
      * 提示词里挂一堆"叫了也没用"的工具, 会稀释真正有用的那些。
    所以只有真的能应答的节点才出现在工具表里。
    """
    out = {}
    for target in TARGETS:
        if not SHARED:
            continue
        ok, payload = call("http://" + target, "/peer/agent")
        if not ok or not payload.get("ok"):
            continue
        if not payload.get("agent_ok"):
            continue
        node = str(payload.get("node") or target).strip() or target
        out[node] = {
            "target": target,
            "site": str(payload.get("site") or "").strip(),
            "endpoint": str(payload.get("endpoint") or "").strip(),
        }
    return out


def discover() -> list:
    """把每个节点的工具聚合成一张列表（每次 tools/list 都重新发现，
    这样对端新起的节点/新加的工具不用重启本桥就能被看到）。"""
    out = []
    filtered_off.clear()
    for target in TARGETS:
        base = "http://" + target
        if not SHARED:
            continue
        ok, payload = call(base, "/peer/tools")
        if not ok or not payload.get("ok"):
            continue
        node = str(payload.get("node") or target).strip() or target
        site = str(payload.get("site") or "").strip()
        for t in payload.get("tools", []):
            name = str(t.get("name", "")).strip()
            if not name:
                continue
            if not tool_allowed(name, t.get("peer_callable")):
                filtered_off.append(f"{node}__{name}")
                continue
            desc = str(t.get("desc", "")).strip()
            when = str(t.get("when_to_use", "")).strip()
            if site:
                desc = f"[{site}] {desc}"
            # 能力层信息拼进 description: agent 看的是 description, 把「何时用」
            # 塞在这里才能真正影响它的选择(实测只给 desc 时会用 fs 乱翻文件)。
            if when:
                desc = f"{desc}。何时用: {when}" if desc else f"何时用: {when}"
            out.append({
                "node": node,
                "target": target,
                "tool": name,
                "name": f"{node}__{name}",
                "description": desc or f"{node} 的 {name}",
            })
    return out


filtered_off: list = []   # discover() 每次填充: 本次被策略拦下的工具名


def tool_schema(name: str, desc: str):
    """inputSchema。peer 侧目前只接受可选的 arguments 对象，这里统一给
    {arguments: object} —— agent 传什么都会原样转给对端。"""
    return {
        "name": name,
        "description": desc,
        "inputSchema": {
            "type": "object",
            "properties": {
                "arguments": {
                    "type": "object",
                    "description": "透传给对端工具的 arguments 对象（可省略）",
                }
            },
        },
    }


def handle(req) -> None:
    method = req.get("method")
    mid = req.get("id")

    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "lume-peer-bridge", "version": "0.1.0"},
        }})
        return

    if method in ("initialized", "notifications/initialized"):
        return  # 通知无需回包

    if method == "tools/list":
        found = discover()
        # ---- 发现层(L0): 第一个工具, 一次调用回答「局域网里有什么」----
        # 没有它, agent 面对 30 个 `lan2__fs__*` 会自己用 fs 去翻文件找答案
        # (实测烧了 7 轮); 有了它是一次工具调用。
        tools = [{
            "name": "_peers__discover",
            "description": ("列出局域网内所有可访问的 lume 应用, 以及每个应用能做什么、"
                            "什么场景该用。**当你不知道某个需求该用哪个工具、或想了解"
                            "局域网里有哪些应用时, 先调这个**, 不要用 fs/memory 等工具"
                            "去翻文件或猜。也用于确认某个应用的名字以便选择 "
                            "<应用名>__<工具名> 形式的工具。"),
            "inputSchema": {"type": "object", "properties": {}},
        }]
        # ---- 工具全量暴露(两段式加载试过, 回滚了) ----
        #
        # 曾按 skills 的思路改成: tools/list 只给每个应用一个 <node>__use
        # (返回该应用的目录), agent 读过之后再调具体工具。想法是让上下文
        # 随节点数不增长。
        #
        # **实测不可行**(2026-10-30): agent 会反复调用 <node>__use 本身, 并给它
        # 塞 {"tool": "crm.stats"} 之类的参数, 拿回同一份目录后重试 ——
        # 连调 4 次 __use + 5 次 think, 始终不改成调用 name 里那个真正干活的
        # 工具, 2 分半耗尽轮数超时。改了描述(明确写"只返回说明, 请改为调用
        # 清单里的名字")也没用。
        #
        # 结论: 两段式依赖 agent"读完目录主动换工具名", 当前模型做不到;
        # 它倾向重复调用名字看起来最相关的那个。skills 之所以可行, 是因为
        # skill 的加载由 harness 完成(模型只读文本), 不需要模型自己切换调用目标。
        #
        # 所以维持全量暴露。工具数真到几十个时, 更可行的方向是**在 bridge 侧
        # 按问题裁剪**(只暴露与本次问题相关的应用), 而不是让模型做两段跳。
        # ---- 按应用聚合: 一个应用一个工具, 用 action 选具体操作 ----
        #
        # 为什么是这个方案, 而不是"两段式加载(tools/list 只给目录)":
        #
        #   * MCP 的 tools/list **不带任何参数**(agent-httpd 那边传的是 NULL,
        #     且只在启动时调一次), 所以 bridge 根本拿不到用户问题 —— 想"按问题
        #     裁剪"没有信息可用。
        #   * 两段式(先 __use 拿目录再调具体工具)实测失败: agent 会反复调用
        #     __use 本身并塞 {"tool":"x"}, 4 次后仍在原地, 2 分半超时。
        #     它要求模型**读完说明后主动改换调用目标**, 当前模型做不到。
        #
        # 聚合是纯接口设计, 不需要模型做任何"跳转":
        #   原来: 每个应用 2-3 个工具 → 3 个应用 8 个工具
        #   现在: 每个应用 1 个工具, action 枚举具体操作 → 3 个工具
        # agent 仍然是"选一个工具 + 填参数", 与 skills 的加载方式同构 ——
        # 系统把选项收敛好, 模型只需选。
        #
        # 具体的 <node>__<tool> 名字**仍然有效**(见 tools/call 的通用分发),
        # 所以老的调用方式、审计记录、文档都不会失效。
        apps = discover_apps()
        for a in apps:
            if not a["reachable"] or not a["tools"]:
                continue
            node = a["node"]
            acts = [t["raw"] for t in a["tools"]]
            hint = "; ".join(
                f"{t['raw']}={t['desc'] or t['raw']}" + (f"（{t['when']}）" if t["when"] else "")
                for t in a["tools"]
            )
            desc = (a["summary"] or f"{node} 应用") + \
                   f" 用 action 指定具体操作, 可选: " + "; ".join(acts) + "。" + \
                   f" 各操作含义: " + hint
            tools.append({
                "name": f"{node}__query",
                "description": desc,
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": acts,
                            "description": "要执行的具体操作（从可选列表里选一个）",
                        },
                        "arguments": {
                            "type": "object",
                            "description": "该操作的参数对象（多数操作可省略）",
                        },
                    },
                    "required": ["action"],
                },
            })
        # ---- 委派层: 问对端的 agent ----
        # 与上面那些 <node>__<tool> 的本质区别: 那些是"取数据"(对端执行一条
        # 查询就返回), 这个是"请求判断"(对端自己决定查什么、怎么答)。
        # 只对真正有 agent 的节点暴露 —— 纯数据节点没有这条路由, 给了工具
        # agent 会去调, 拿到 404 后反复重试。
        for node, info in probe_agents().items():
            site = info["site"] or node
            tools.append({
                "name": f"{node}__ask",
                "description": (
                    f"向 {site}（节点 {node}）的 agent 提问，由**它自己**决定查什么、"
                    f"怎么答。适合需要该应用专业判断的问题，例如"
                    f"「这批数据里有哪些异常」「哪些值得关注」。"
                    f"只想要一个数字时用 {node}__<工具名> 更快——委派要多一次模型调用。"
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string",
                                     "description": "要问对方 agent 的问题，自然语言即可"},
                    },
                    "required": ["question"],
                },
            })
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}})
        return

    if method == "tools/call":
        params = req.get("params") or {}
        want = str(params.get("name") or "")
        args = params.get("arguments") or {}

        # ---- 聚合入口: <node>__query ----
        # 按 action 参数转成对端的 <node>__<tool> 调用。这条路径让 agent
        # 只需"选一个工具 + 填 action", 不必在多应用的工具名之间挑。
        if want.endswith("__query"):
            node = want[: -len("__query")]
            action = str(args.get("action") or "").strip()
            if not action:
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text":
                        f"缺少 action。{node} 可用的操作见该工具的 action 枚举。"}],
                    "isError": True}})
                return
            # 校验 action 确实是该应用声明过的工具 —— 否则这里就成了
            # "打穿到对端任意工具"的跳板(和 <node>__<tool> 的白名单同理)。
            app = {a["node"]: a for a in discover_apps()}.get(node)
            valid = {t["raw"] for t in (app or {}).get("tools", [])}
            if action not in valid:
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text":
                        f"{node} 没有声明操作 '{action}'。可选: " + ", ".join(sorted(valid))}],
                    "isError": True}})
                return
            # 转成通用分发: 复用 <node>__<tool> 那条路径
            want = f"{node}__{action}"
            args = args.get("arguments") or {}

        # ---- 委派: <node>__ask ----
        # 形如 "scanner__ask"。注意不能和 discover() 的名字撞: 那边是
        # "<node>__<tool>", 这里多一个保留后缀 ask。
        if want.endswith("__ask"):
            node = want[: -len("__ask")]
            agents = probe_agents()
            info = agents.get(node)
            if not info:
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text":
                        f"{node} 目前没有可用的 agent（节点不可达, 或它的 "
                        f"agent-httpd 未启动）。改用 {node}__<工具名> 直接取数据。"}],
                    "isError": True,
                }})
                return
            q = str(args.get("question") or "").strip()
            if not q:
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": "缺少 question 参数。"}],
                    "isError": True,
                }})
                return
            # 跳数预算: 带上 hop(已委派次数) 与 trace(经过的节点)。
            #
            # 为什么主 agent 侧也要记: 对端只能看到"我这一跳进来时的计数",
            # 它无法知道更上游转过几次。所以发起方每委派一次就把 hop+1、
            # 把自己的名字追加进 trace —— 对端据此判断"是不是已经成环"。
            #
            # 默认从 1 开始(本次就是第 1 跳); 对端再委派时会是 2。
            my_name = NODE
            req_body = {
                "question": q,
                "hop": 1,
                "trace": [my_name],
            }
            ok, payload = call("http://" + info["target"], "/peer/ask",
                               json.dumps(req_body, ensure_ascii=False))
            if not ok or not payload.get("ok"):
                err = payload.get("error") or ("不可达" if not ok else "对端拒绝")
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text":
                        f"向 {node} 的 agent 提问失败: {err}"}],
                    "isError": True,
                }})
                return
            answer = str(payload.get("answer") or "").strip() or "(对端返回空回答)"
            # 把对方的**工具调用痕迹**一并带回来: 主 agent 据此能判断
            # "这个结论是基于哪些数据得出的", 而不是转述一个黑箱结论。
            notes = [str(n) for n in (payload.get("notes") or [])]
            text = answer
            if notes:
                text += "\n\n[对方调用的工具] " + "; ".join(notes[:10])
            # 把跳数/链路也回给 agent: 它能看到"这次委派走到了第几跳",
            # 于是不会在接近上限时还继续转派。
            hop_used = payload.get("hop")
            if hop_used is not None:
                text += f"\n\n[委派跳数] {hop_used}/{MAX_HOPS}"
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}], "isError": False,
            }})
            return

        hit = next((d for d in discover() if d["name"] == want), None)
        if not hit:
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text",
                             "text": f"未知工具 '{want}'。可用: "
                                     + ", ".join(d["name"] for d in discover())}],
                "isError": True,
            }})
            return

        payload = {"tool": hit["tool"], "arguments": args.get("arguments", args) or {}}
        body = json.dumps(payload, ensure_ascii=False)
        ok, res = call("http://" + hit["target"], "/peer/invoke", body)
        if not ok:
            text = f"[{hit['node']}] 不可达: {res.get('error', '')}"
            is_err = True
        elif not res.get("ok"):
            text = f"[{hit['node']}] {res.get('error', '调用失败')}"
            is_err = True
        else:
            r = res.get("result", res)
            text = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False, indent=2)
            is_err = False
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": text}],
            "isError": is_err,
        }})
        return

    if mid is not None:
        send({"jsonrpc": "2.0", "id": mid,
              "error": {"code": -32601, "message": f"method not found: {method}"}})


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None,
                  "error": {"code": -32700, "message": "parse error"}})
            continue
        try:
            handle(req)
        except Exception as e:  # noqa: BLE001 — 一个坏请求不能杀掉整个会话
            mid = req.get("id")
            if mid is not None:
                send({"jsonrpc": "2.0", "id": mid,
                      "error": {"code": -32603, "message": str(e)}})


if __name__ == "__main__":
    main()
