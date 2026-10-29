/* 应用互访面板: 展示局域网内可通讯的 lume peer 节点。
 *
 * 数据来自 /api/peers —— 但它**不是容器自己探测的**: 容器在 Docker NAT
 * 网段里连不到宿主 loopback 上的 peer 端口, 所以由宿主 scripts/peers_fetch.py
 * 定时探测并写 .data/peers.json, 这里只读快照(与 devices/router/dns 同一套分工)。
 */
import React from "react";
import type { PeersPayload, PeerNode } from "../api";

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

      <div className="hint peersfoot">
        探测在<strong>宿主</strong>执行：看板跑在 Docker NAT 网段，连不到宿主
        loopback 上的 peer 端口；<code>PEER_SHARED</code> 也不进容器。
      </div>
    </>
  );
}
