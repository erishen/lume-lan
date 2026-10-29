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

# 写/破坏性动作的命名特征。宁可多拦: 漏掉一个写工具的代价远大于多拦一个读工具。
DENY_TOKENS = (
    "write", "edit", "create", "delete", "remove", "move", "rename",
    "put", "patch", "update", "insert", "drop", "truncate", "mkdir",
    "add_", "set_", "append", "save", "upload", "exec", "run_", "shutdown",
)


def tool_allowed(tool_name: str) -> bool:
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
            if not tool_allowed(name):
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
            if not tool_allowed(name):
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
        tools += [tool_schema(d["name"], d["description"]) for d in found]
        # 把被策略拦下的工具作为一条只读"说明工具"暴露给 agent —— 让它知道
        # 「存在但被禁用」, 而不是以为对端没有这些能力而绕路或编造。
        if filtered_off:
            tools.append({
                "name": "_peers__blocked",
                "description": (f"只读策略拦下了 {len(filtered_off)} 个写/破坏性工具, "
                                f"它们不在本次工具表里。清单: "
                                + ", ".join(filtered_off[:20])
                                + (" …" if len(filtered_off) > 20 else "")
                                + "（如需放开, 设 PEER_EXPOSE=all 或白名单）"),
                "inputSchema": {"type": "object", "properties": {}},
            })
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}})
        return

    if method == "tools/call":
        params = req.get("params") or {}
        want = str(params.get("name") or "")
        args = params.get("arguments") or {}

        # 找到这个 name 属于哪个节点（重新发现一次，避免拿到过期映射）
        if want == "_peers__discover":
            apps = discover_apps()
            if not apps:
                txt = "当前没有可访问的 peer 应用（未配置 PEER_SHARED/PEER_TARGETS，或对端都不可达）。"
            else:
                parts = []
                for a in apps:
                    if not a["reachable"]:
                        parts.append(f"## {a['node']}（不可达：{a['error']}）")
                        continue
                    title = f"## {a['node']}" + (f" — {a['site']}" if a["site"] else "")
                    body = [title]
                    if a["summary"]:
                        body.append(a["summary"])
                    for t in a["tools"]:
                        # 发现层只给**摘要**: desc 可能是一大段英文(think__ 那种
                        # 上千字符), 全塞进来会吃光上下文。细节留在工具自身的
                        # description 里, agent 真要调时再看到。
                        d = t["desc"]
                        if len(d) > 80:
                            d = d[:77] + "…"
                        line = f"- {t['name']}：{d}"
                        if t["when"]:
                            line += f"（何时用：{t['when']}）"
                        body.append(line)
                    parts.append("\n".join(body))
                txt = "\n\n".join(parts)
                if filtered_off:
                    txt += (f"\n\n注：{len(filtered_off)} 个写/破坏性工具已按只读策略隐藏。")
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": txt}], "isError": False,
            }})
            return

        if want == "_peers__blocked":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text":
                             "这是说明性条目, 不是可调用工具。被拦下的写操作: "
                             + ", ".join(filtered_off[:40])}],
                "isError": False,
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
