/* 应用互访面板: 展示局域网内可通讯的 lume peer 节点。
 *
 * 数据来自 /api/peers —— 但它**不是容器自己探测的**: 容器在 Docker NAT
 * 网段里连不到宿主 loopback 上的 peer 端口, 所以由宿主 scripts/peers_fetch.py
 * 定时探测并写 .data/peers.json, 这里只读快照(与 devices/router/dns 同一套分工)。
 */
import React from "react";
import type { PeersPayload, PeerNode, PendingTask, AuditRow } from "../api";

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

function PeerCard({ p }: { p: PeerNode }) {
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
      </div>
      {p.reachable ? (
        <>
          {p.tools && p.tools.length > 0 ? (
            <div className="peertools">
              {p.tools.map((t) => (
                <span className="peertool" key={t.name} title={t.desc || ""}>
                  {t.name}
                </span>
              ))}
            </div>
          ) : (
            <div className="hint">未声明能力清单</div>
          )}
          <InfoChips info={p.info} />
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

export function PendingPanel({ pending }: { pending?: PendingTask[] }) {
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
          </div>
          <StageChips t={t} />
          {t.waiting_for ? <div className="pendingwait">{t.waiting_for}</div> : null}
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
  approval_step: "单级通过",
  task_finished: "任务结束",
};

export function AuditPanel({ audit }: { audit?: AuditRow[] }) {
  if (!audit || audit.length === 0) {
    return <div className="hint">暂无审计记录。</div>;
  }
  return (
    <table className="audittable">
      <thead>
        <tr><th>时间</th><th>事件</th><th>角色</th><th>决策</th><th>说明</th></tr>
      </thead>
      <tbody>
        {audit.slice(0, 30).map((r, i) => (
          <tr key={i} className={r.decision === "DENY" ? "auditdeny" : ""}>
            <td className="mono">{fmtAgo(r.at)}</td>
            <td>{AUDIT_LABEL[r.event] || r.event}</td>
            <td className="mono">{r.role || r.claimed_role || r.required_role || "—"}</td>
            <td className="mono">{r.decision || "—"}</td>
            <td className="auditreason">{r.reason || ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function PeersPanel({ peers }: { peers: PeersPayload | null }) {
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
            <PeerCard key={p.target} p={p} />
          ))}
        </div>
      )}

      <h3 className="peersec">待办审批</h3>
      <PendingPanel pending={peers.pending} />

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
