/* 应用互访面板: 展示局域网内可通讯的 lume peer 节点。
 *
 * 数据来自 /api/peers —— 但它**不是容器自己探测的**: 容器在 Docker NAT
 * 网段里连不到宿主 loopback 上的 peer 端口, 所以由宿主 scripts/peers_fetch.py
 * 定时探测并写 .data/peers.json, 这里只读快照(与 devices/router/dns 同一套分工)。
 */
import React from "react";
import type { PeersPayload, PeerNode, PeerTool, PendingTask, AuditRow } from "../api";
import { apiApprove, apiPeerInvoke, apiPeerAsk, apiChat } from "../api";
import { renderMarkdown } from "./markdown";

function fmtAgo(ts?: number): string {
  if (!ts) return "—";
  const s = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  if (s < 60) return s + " 秒前";
  if (s < 3600) return Math.floor(s / 60) + " 分钟前";
  return Math.floor(s / 3600) + " 小时前";
}

/* info 里的已知计数键 -> 中文标签。未知键不展示(避免把内部字段漏到界面上)。 */
const INFO_LABELS: { key: string; label: string }[] = [
  { key: "post_count", label: "文章" },
  { key: "published_count", label: "已发布" },
  { key: "total_views", label: "浏览" },
  { key: "tag_count", label: "标签" },
];

function InfoChips({ info }: { info?: Record<string, unknown> }) {
  if (!info) return null;
  const items = INFO_LABELS
    .map(({ key, label }) => ({ label, v: info[key] }))
    .filter((x) => typeof x.v === "number");
  if (items.length === 0) return null;
  return (
    <div className="peerchips">
      {items.map((x) => (
        <span className="peerchip" key={x.label}>
          {x.label} <b>{String(x.v)}</b>
        </span>
      ))}
    </div>
  );
}

/** 调一个工具并展示结果。
 *
 * 结果直接渲染成 JSON 文本而不是"美化"成卡片: 这是**调试/验证**界面,
 * 用户要的是对端到底返回了什么(含字段名), 二次加工反而丢信息。
 */
function ToolRunner({ p, tool, onDone }: {
  p: PeerNode; tool: PeerTool; onDone: () => void;
}) {
  const [busy, setBusy] = React.useState(false);
  const [out, setOut] = React.useState<string | null>(null);
  const [err, setErr] = React.useState("");

  const run = async () => {
    setBusy(true); setErr(""); setOut(null);
    const r = await apiPeerInvoke(p.target, tool.name, {});
    setBusy(false);
    if (!r.ok) { setErr(r.error || "调用失败"); return; }
    setOut(typeof r.result === "string"
      ? r.result
      : JSON.stringify(r.result, null, 2));
    onDone();          // 审计里多了一条, 让上层重拉
  };

  return (
    <div className="toolrun">
      <button className="btntool" disabled={busy} title={tool.desc || ""}
              onClick={() => void run()}>
        {busy ? "调用中…" : tool.name}
      </button>
      {err ? <span className="approveerr">{err}</span> : null}
      {out !== null ? (
        <pre className="toolresult">{out.length > 1200 ? out.slice(0, 1200) + "…" : out}</pre>
      ) : null}
    </div>
  );
}

/** 向对端的 agent 提一个问题。
 *
 * 只在探测到 /peer/agent 可用时渲染 —— 纯数据节点(博客)没有这个路由,
 * 给了输入框反而会让人以为是坏的。
 */
function AskBox({ p, onDone }: { p: PeerNode; onDone: () => void }) {
  const [q, setQ] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [ans, setAns] = React.useState("");
  const [err, setErr] = React.useState("");

  const ask = async () => {
    if (!q.trim()) return;
    setBusy(true); setErr(""); setAns("");
    const r = await apiPeerAsk(p.target, q.trim());
    setBusy(false);
    if (!r.ok) { setErr(r.error || "提问失败"); return; }
    setAns(r.answer || "(对端返回了空回答)");
    onDone();
  };

  return (
    <div className="askbox">
      <div className="askrow">
        <input className="askinput" value={q} disabled={busy}
               placeholder={"向 " + (p.name || p.target) + " 的 agent 提问…"}
               onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") void ask(); }} />
        <button className="btnask" disabled={busy || !q.trim()} onClick={() => void ask()}>
          {busy ? "等待…" : "提问"}
        </button>
      </div>
      {err ? <div className="approveerr">{err}</div> : null}
      {ans ? <div className="askanswer">{ans}</div> : null}
    </div>
  );
}

function PeerCard({ p, onChanged }: { p: PeerNode; onChanged?: () => void }) {
  const cls = p.reachable ? "peerok" : "peerdown";
  return (
    <div className={"peerbox " + cls}>
      <div className="peerhead">
        <span className={"dot " + (p.reachable ? "on" : "off")} />
        <span className="peername">{p.site || p.name || p.target}</span>
        {p.site && p.name ? <span className="peermeta">{p.name}</span> : null}
        <span className="peermeta mono">{p.target}</span>
        {p.reachable && p.latency_ms !== undefined ? (
          <span className="peermeta">{p.latency_ms} ms</span>
        ) : null}
        {p.agent ? <span className="peerchip">agent</span> : null}
      </div>
      {p.reachable ? (
        <>
          {p.tools && p.tools.length > 0 ? (
            <div className="peertools">
              {p.tools.map((t) => (
                <ToolRunner key={t.name} p={p} tool={t} onDone={() => onChanged?.()} />
              ))}
            </div>
          ) : (
            <div className="hint">未声明能力清单</div>
          )}
          <InfoChips info={p.info} />
          {p.agent ? <AskBox p={p} onDone={() => onChanged?.()} /> : null}
        </>
      ) : (
        <div className="hint peererr">不可达: {p.error || "未知原因"}</div>
      )}
    </div>
  );
}

/* ---- 待办审批 ----
 * 为什么需要这块 UI: 审批链要求"人点一下确认", 而协议层只接受
 * `<role>: approve` 这种字符串。没有表单的话, 每次批准都得手打角色名,
 * 打错就等于没批(还会留一条 DENY 审计)。 */
function StageChips({ t }: { t: PendingTask }) {
  if (!t.policy || t.policy.length === 0) {
    return <span className="hint">无审批链(直接执行)</span>;
  }
  return (
    <div className="stageflow">
      {t.policy.map((st, i) => {
        const done = t.approved.includes(st.role);
        const isNext = !done && t.approved.length === i;
        const label = st.stage === "final" ? "人工终审" : st.node || st.role;
        return (
          <React.Fragment key={st.role + i}>
            {i > 0 ? <span className="stagearrow">→</span> : null}
            <span className={"stagechip" + (done ? " done" : isNext ? " next" : "")}>
              {done ? "✓ " : ""}{label}
            </span>
          </React.Fragment>
        );
      })}
    </div>
  );
}

/** 一条待办的批准/驳回按钮。
 *
 * 只在服务端说 can_act=true 时出现 —— 也就是"这一关轮到本节点"。
 * 前端不算这个判定: 规则(谁是下一关、能不能代办人工终审)只写在服务端,
 * 前端复刻一份迟早会和后端漂移, 表现为"按钮能点但请求被拒"。
 *
 * 驳回要填理由: 审批留痕的价值在于"为什么"，只记一个 REJECT 事后无法复盘。
 */
function ApproveActions({ t, onDone }: { t: PendingTask; onDone: () => void }) {
  const [busy, setBusy] = React.useState(false);
  const [note, setNote] = React.useState("");
  const [wantReject, setWantReject] = React.useState(false);
  const [err, setErr] = React.useState("");

  const submit = async (decision: "approve" | "reject") => {
    if (decision === "reject" && !wantReject) {
      setWantReject(true);          // 先展开理由输入, 不直接提交
      return;
    }
    setBusy(true);
    setErr("");
    const r = await apiApprove(t.id, decision, decision === "reject" ? note : "");
    setBusy(false);
    if (!r.ok) {
      // 服务端拒绝时带的是"该由谁审"这类完整句子, 直接显示, 不自己拼
      setErr(r.error || "审批失败");
      return;
    }
    setWantReject(false);
    setNote("");
    onDone();
  };

  return (
    <div className="pendingactions">
      {wantReject ? (
        <input
          className="rejectnote"
          placeholder="驳回理由（会写进审批记录）"
          value={note}
          disabled={busy}
          onChange={(e) => setNote(e.target.value)}
        />
      ) : null}
      <button className="btnapprove" disabled={busy} onClick={() => void submit("approve")}>
        {busy ? "提交中…" : "批准"}
      </button>
      <button className="btnreject" disabled={busy} onClick={() => void submit("reject")}>
        驳回
      </button>
      {err ? <span className="approveerr">{err}</span> : null}
    </div>
  );
}

export function PendingPanel({ pending, onChanged }: {
  pending?: PendingTask[];
  onChanged?: () => void;
}) {
  if (!pending || pending.length === 0) {
    return <div className="hint">没有待办审批。审批链由任务自己声明（`require_approval`）。</div>;
  }
  return (
    <div className="pendinglist">
      {pending.map((t) => (
        <div className="pendingbox" key={t.id + t.source}>
          <div className="pendinghead">
            <span className={"dot " + (t.state === "input-required" ? "idle" : "on")} />
            <span className="pendingtool mono">{t.tool || "(未知工具)"}</span>
            {t.domain ? <span className="peerchip">{t.domain}</span> : null}
            <span className="peermeta mono">{t.id.slice(0, 12)}</span>
            <span className="peermeta">等 {fmtAgo(t.since)}</span>
            {/* 这一关归谁 —— 有按钮时按钮已经说明了, 只在不能批时提示 */}
            {!t.can_act && t.blocked_reason ? (
              <span className="blockedchip">{t.blocked_reason}</span>
            ) : null}
          </div>
          <StageChips t={t} />
          {t.waiting_for ? <div className="pendingwait">{t.waiting_for}</div> : null}
          {t.can_act ? <ApproveActions t={t} onDone={() => onChanged?.()} /> : null}
        </div>
      ))}
    </div>
  );
}

/* ---- 审计 ----
 * 任务的 history 只记对话, 不记决策; "谁在什么时候批了什么"只在审计里。
 * 这里显示尾部若干条, 完整文件仍在 .data/a2a-audit.jsonl。 */
const AUDIT_LABEL: Record<string, string> = {
  approval_required: "请求审批",
  approval_granted: "审批通过",
  approval_denied: "越权被拒",
  approval_step: "单级审批",
  task_finished: "任务结束",
};

/* 事件名只说明"发生了什么类别", 方向要看 decision。
 * approval_step 既可能是 APPROVE 也可能是 REJECT —— 一律标"单级通过"会把
 * 驳回记成通过, 翻审计时正好读反。所以按 (event, decision) 联合给标签。 */
function auditLabel(r: AuditRow): string {
  if (r.event === "approval_step") {
    if (r.decision === "REJECT") return "单级驳回";
    if (r.decision === "APPROVE") return "单级通过";
    return "单级审批";
  }
  if (r.event === "a2a_call") {
    // outcome 比"成功/失败"更有信息量: 分清是网络不通、被对方拒、还是
    // 对方答了但状态不对 —— 排障时这三种的处理方式完全不同。
    const M: Record<string, string> = {
      ok: "调用成功",
      denied: "对端拒绝",
      unreachable: "不可达",
      bad_response: "响应异常",
    };
    return M[r.outcome || ""] || "A2A 调用";
  }
  return AUDIT_LABEL[r.event] || r.event;
}

/* 一行的"对象"列: 审批事件看角色, A2A 调用看目标+方法。
 * 合成一列而不是各加一列, 是因为两者互斥(一条记录只属于一类),
 * 分开会让表格多出半列永远空闲的空白。 */
function auditSubject(r: AuditRow): string {
  if (r.event === "a2a_call") {
    const t = r.target || "—";
    const m = r.method || "";
    return m ? t + " · " + m : t;
  }
  return r.role || r.claimed_role || r.required_role || "—";
}

/* 一行的"说明": 审批看 reason, 调用看 detail + 耗时/状态码。
 * 耗时是排障时第一个要看的数字(慢 vs 挂), 所以放在最前面。 */
function auditDetail(r: AuditRow): string {
  if (r.event === "a2a_call") {
    const bits: string[] = [];
    // 来源放最前: 同一次调用可能在两侧各有一条, 先分清"这是谁记的",
    // 才看得出"发出去没到"还是"到了也处理了"。
    if (r.source) bits.push(r.source === "lan-ui" ? "看板发出" : r.source);
    if (r.elapsed_ms) bits.push(r.elapsed_ms + "ms");
    if (r.status) bits.push("HTTP " + r.status);
    if (r.tool) bits.push(r.tool);
    if (r.detail) bits.push(r.detail);
    return bits.join(" · ");
  }
  const out: string[] = [];
  if (r.origin) out.push("来源:" + r.origin);
  if (r.reason) out.push(r.reason);
  return out.join(" · ");
}

export function AuditPanel({ audit }: { audit?: AuditRow[] }) {
  if (!audit || audit.length === 0) {
    return <div className="hint">暂无审计记录。</div>;
  }
  return (
    <table className="audittable">
      <thead>
        <tr><th>时间</th><th>事件</th><th>对象</th><th>结果</th><th>说明</th></tr>
      </thead>
      <tbody>
        {audit.slice(0, 30).map((r, i) => (
          <tr key={i} className={
            r.decision === "DENY" || r.outcome === "denied" ||
            r.outcome === "unreachable" ? "auditdeny" : ""}>
            <td className="mono">{fmtAgo(r.at)}</td>
            <td>{auditLabel(r)}</td>
            <td className="mono">{auditSubject(r)}</td>
            <td className="mono">
              {r.event === "a2a_call" ? (r.outcome || "—") : (r.decision || "—")}
            </td>
            <td className="auditreason">{auditDetail(r)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ---- 和 agent 对话 ----
 *
 * 为什么需要这块: 「应用互访」之前只有"点按钮看 JSON", 那是调试视角。
 * agent 真正的价值是"问一句话, 它自己去查该查的节点" —— 没有输入框, 这个
 * 能力就只能靠 curl 用, 页面等于半个展示牌。
 *
 * 数据流: 这里 → /api/chat → 本机 agent-httpd → 它按需调 MCP 暴露的
 * 跨节点工具(crm/scanner/blog) → 组织成回答。
 *
 * 交互取舍:
 *   - 后端是同步的(lume 的 http_post 收完整响应才返回), 所以显示"思考中…"
 *     并把已用时计上, 否则长回答时界面像卡死;
 *   - 回答下面列出 notes(它调了哪些工具) —— 不给这个, 用户看到的是"凭空
 *     一个答案", 无法判断数据可不可信;
 *   - 多轮: sessionId 一直带着, agent-httpd 按它存 transcript, 所以能追问。
 */
/* 示例问题。挑的都是**必须跨节点查**的: 各自对应一个应用的真实数据,
 * 问完能立刻看出 agent 有没有真的去调工具(回答下方会显示它调了什么)。 */
const SUGGESTIONS = [
  "CRM 现在有几个客户? 商机总额多少?",
  "扫描器最新数据到哪天? 覆盖多少只股票?",
  "博客写了多少篇文章? 浏览量多少?",
  "三个应用的数据分别是什么时候的?",
];

type ChatTurn = {
  role: "user" | "agent";
  text: string;
  notes?: string[];
  ms?: number;
  error?: boolean;
};

function ChatPanel() {
  const [turns, setTurns] = React.useState<ChatTurn[]>([]);
  const [q, setQ] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [secs, setSecs] = React.useState(0);
  const sid = React.useRef<string>("");
  const endRef = React.useRef<HTMLDivElement | null>(null);

  React.useEffect(() => {
    if (!busy) { setSecs(0); return; }
    const t = window.setInterval(() => setSecs((s) => s + 1), 1000);
    return () => window.clearInterval(t);
  }, [busy]);

  React.useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [turns, busy]);

  /** 真正发一条。chip 和输入框都走这里 —— 逻辑只有一份。 */
  const ask = async (text: string) => {
    text = text.trim();
    if (!text || busy) return;
    setQ("");
    setTurns((t) => [...t, { role: "user", text }]);
    setBusy(true);
    const r = await apiChat(text, sid.current);
    setBusy(false);
    if (r.ok) {
      if (!sid.current) sid.current = "lan-ui-" + Date.now().toString(36);
      setTurns((t) => [...t, {
        role: "agent", text: r.answer || "(空回答)",
        notes: (r.notes || []).filter((n) => n.includes("tool ")),
        ms: r.elapsed_ms,
      }]);
    } else {
      setTurns((t) => [...t, { role: "agent", text: r.error || "调用失败", error: true }]);
    }
  };

  return (
    <div className="chatbox">
      <div className="chatlog">
        {turns.length === 0 ? (
          <div className="hint">
            问点什么, agent 会自己决定去查哪些节点。
          </div>
        ) : null}
        {turns.map((t, i) => (
          <div key={i} className={"chatturn " + t.role + (t.error ? " err" : "")}>
            <div className="chatwho">{t.role === "user" ? "我" : "agent"}
              {t.ms !== undefined ? <span className="chatms">{t.ms}s</span> : null}
            </div>
            {/* agent 的回答是 Markdown(表格/列表/粗体), 必须渲染 ——
                直接 pre-wrap 显示时, 管道表格会原样露出一堆 | 和 ---, 
                完全读不下去。用与 lume-crm 同一份渲染器, 行为一致。 */}
            <div className="chattext">
              {t.role === "agent" && !t.error
                ? renderMarkdown(t.text)
                : t.text}
            </div>
            {t.notes && t.notes.length > 0 ? (
              <div className="chatnotes">
                {t.notes.map((n, j) => <span className="chatnote" key={j}>{n}</span>)}
              </div>
            ) : null}
          </div>
        ))}
        {busy ? (
          <div className="chatturn agent">
            <div className="chatwho">agent</div>
            <div className="chattext thinking">思考中… {secs}s</div>
          </div>
        ) : null}
        <div ref={endRef} />
      </div>
      {/* 示例问题**常驻**: 之前只在对话为空时显示, 问完就消失, 用户不知道
          还能问什么。做成可点的 chip, 点一下直接发送 —— 既当提示又当快捷入口。 */}
      <div className="chatexamples">
        {SUGGESTIONS.map((s) => (
          <button key={s} className="chatex" disabled={busy}
                  onClick={() => void ask(s)}>{s}</button>
        ))}
      </div>
      <div className="askrow">
        <input className="askinput" value={q} disabled={busy}
               placeholder="问 agent：它会自己调 crm/scanner/blog 的工具"
               onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") void ask(q); }} />
        <button className="btnask" disabled={busy || !q.trim()} onClick={() => void ask(q)}>
          {busy ? "等待…" : "发送"}
        </button>
        {turns.length > 0 ? (
          <button className="btnask" disabled={busy}
                  onClick={() => { setTurns([]); sid.current = ""; }}>清空</button>
        ) : null}
      </div>
    </div>
  );
}

/** Agent Tab: 与 agent 对话 + 它此刻能用的工具。
 *
 * 为什么带上"能力清单": 用户问问题前应该知道 agent 能查什么,
 * 否则只能瞎试。清单来自 peers 快照(各节点声明的工具), 与
 * agent-httpd 通过 peer_bridge 看到的是同一份。
 */
export function AgentPanel({ peers }: { peers: PeersPayload | null }) {
  const nodes = (peers?.peers || []).filter((p) => p.reachable);
  const toolCount = nodes.reduce((n, p) => n + ((p.tools || []).length), 0);
  return (
    <>
      <div className="agenthead">
        <div className="agenttitle">和本机 agent 对话</div>
        <div className="agentmeta">
          它能看到 {nodes.length} 个节点的 {toolCount} 个工具，会自己判断该查哪个。
          {nodes.length === 0 ? <span className="approveerr">（当前没有可达节点，先去「应用互访」看看）</span> : null}
        </div>
        {nodes.length > 0 ? (
          <div className="agentcaps">
            {nodes.map((p) => (
              <span className="agentcap" key={p.target}>
                <b>{p.name || p.target}</b>
                {(p.tools || []).map((t) => (
                  <span className="agentcap1" key={t.name}>{t.name}</span>
                ))}
              </span>
            ))}
          </div>
        ) : null}
      </div>
      <ChatPanel />
    </>
  );
}

export function PeersPanel({ peers, onChanged }: {
  peers: PeersPayload | null;
  /** 审批成功后回调: 让上层重新拉一遍 /api/peers(状态、链、审计都会变) */
  onChanged?: () => void;
}) {
  if (!peers) {
    return <div className="hint">正在读取 peer 快照…</div>;
  }
  if (!peers.available) {
    return (
      <div className="peersempty">
        <div className="peerempty-title">尚未探测 peer 节点</div>
        <div className="hint">
          在 <code>.env</code> 配 <code>PEER_SHARED</code>（与对端一致）和{" "}
          <code>PEER_TARGETS</code>，然后跑 <code>make peers-fetch</code>。
        </div>
        {peers.note ? <div className="hint">{peers.note}</div> : null}
      </div>
    );
  }
  if (!peers.enabled) {
    return (
      <div className="peersempty">
        <div className="peerempty-title">peer 探测未启用</div>
        <div className="hint">{peers.error || "未配置 PEER_SHARED"}</div>
        <div className="hint">
          配好后跑 <code>make peers-fetch</code>（宿主侧探测，容器只读快照）。
        </div>
      </div>
    );
  }

  const list = peers.peers || [];
  return (
    <>
      <div className="kpis">
        <div className="kpi ok">
          <div className="k">可达</div>
          <div className="v">{peers.up_count ?? 0}</div>
          <div className="u">/ {peers.total_count ?? 0} 个节点</div>
        </div>
        <div className="kpi">
          <div className="k">peer 探测于</div>
          <div className="v small">{fmtAgo(peers.fetched_at)}</div>
          <div className="u">{peers.stale ? "已过期" : "新鲜"}</div>
        </div>
        <div className="kpi">
          <div className="k">目标</div>
          <div className="v small">{(peers.targets || []).length}</div>
          <div className="u mono">{(peers.targets || []).join(", ") || "—"}</div>
        </div>
      </div>

      {peers.stale ? (
        <div className="banner warn">
          {/* 明确是「peer 快照」的时长: 页面顶部那条是设备快照的, 两个数字
              本来就不一样, 不区分会让人以为是同一个数据在自相矛盾。 */}
          peer 快照已 {Math.floor((peers.age_secs ?? 0) / 60)} 分钟未更新——宿主{" "}
          <code>make peers-fetch</code> 可刷新
        </div>
      ) : null}

      {list.length === 0 ? (
        <div className="hint">PEER_TARGETS 为空，没有配置任何对端。</div>
      ) : (
        <div className="peersgrid">
          {list.map((p) => (
            <PeerCard key={p.target} p={p} onChanged={onChanged} />
          ))}
        </div>
      )}

      <h3 className="peersec">待办审批</h3>
      <PendingPanel pending={peers.pending} onChanged={onChanged} />

      <h3 className="peersec">审计（最近 30 条）</h3>
      <AuditPanel audit={peers.audit} />

      <div className="hint peersfoot">
        探测在<strong>宿主</strong>执行：看板跑在 Docker NAT 网段，连不到宿主
        loopback 上的 peer 端口；凭据也不进容器。审批与审计从{" "}
        <code>PEER_STATE_DIRS</code> 指定的目录只读采集。
      </div>
    </>
  );
}
