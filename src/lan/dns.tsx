/* DNS 域名访问区: 配置指引 / Top 列表 / 客户端卡片 / 薄壳 Panel + 域名链接工具。 */
import React from "react";
import type { DnsPayload, DnsClient, DnsDomain } from "../api";
import { siteName } from "./sites";

const ADGUARD_URL = "http://localhost:10095";   // AdGuard 控制台宿主端口(>10000 约定)
const DNS_FALLBACK = "223.5.5.5";               // 备用 DNS(AdGuard 没开时兜底不断网)

/** 可折叠配置指引: 3 步骤让设备走 AdGuard DNS。stale 时提示数据是旧的。 */
export function DnsGuide({ dns, dnsTarget }: {
  dns: DnsPayload;
  dnsTarget: string;
}): React.ReactElement {
  const [open, setOpen] = React.useState(true);
  return (
    <div className={"dnsguide" + (open ? "" : " collapsed")}>
      <button className="dnsguide-toggle" onClick={() => setOpen(!open)} type="button">
        {open ? "收起" : "展开"} 配置指引 · 怎么让设备走 AdGuard DNS
      </button>
      {open ? (
        <div className="dnsguide-body">
          <div className="dnsguide-step">
            <span className="dnsguide-num">1</span>
            <div>
              <b>打开 AdGuard 控制台</b>
              <div className="dnsguide-sub">
                <a href={ADGUARD_URL} target="_blank" rel="noreferrer" className="mono">{ADGUARD_URL}</a>
                当前已关闭 web 认证，免登录直接进；改上游 DNS / 白名单过滤都在这里操作
              </div>
            </div>
          </div>
          <div className="dnsguide-step">
            <span className="dnsguide-num">2</span>
            <div>
              <b>设设备 DNS（首选 <span className="mono">{dnsTarget}</span> · 备用 <span className="mono">{DNS_FALLBACK}</span>）</b>
              <div className="dnsguide-sub">
                Mac：系统设置 → 网络 → DNS → 加 <span className="mono">{dnsTarget}</span> 和 <span className="mono">{DNS_FALLBACK}</span><br />
                iPhone：设置 → Wi-Fi → ⓘ → 配置 DNS → 手动 → 同上加<br />
                Android：设置 → Wi-Fi → 长按已连网络（或点 ⓘ）→ 显示高级选项 → IP 设置改「静态」→ DNS1 填 <span className="mono">{dnsTarget}</span>、DNS2 填 <span className="mono">{DNS_FALLBACK}</span>（需同时填对本机 IP/网关；保存后 DNS 生效）<br />
                （或装开源免费的 Blokada 2 App 走 VPN 模式接管 DNS；AdGuard for Android 已改订阅制 14 天试用）<br />
                本机（AdGuard 载体）没开时，设备落到备用 <span className="mono">{DNS_FALLBACK}</span>，不断网
              </div>
            </div>
          </div>
          <div className="dnsguide-step">
            <span className="dnsguide-num">3</span>
            <div>
              <b>验证 DNS 数据流（<code>make dns-fetch</code>）</b>
              <div className="dnsguide-sub">
                配完 DNS 后跑 <code>make dns-fetch</code>（或 <code>make dev</code> 自动循环），本区即刷新；也可点右上「刷新」按钮重拉 <span className="mono">/api/dns</span>
              </div>
            </div>
          </div>
          {dns.stale ? (
            <div className="dnsguide-stale">
              ⚠️ 当前是 <b>{new Date((dns.fetched_at ?? 0) * 1000).toLocaleString("zh-CN")}</b> 的旧数据（AdGuard 现在不可达），修好后自动恢复。
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/* 域名 → 可点击链接。以下情况打开也打不开, 不给链接:
 *   PTR 反解析记录(.in-addr.arpa)无网站; 被 AdGuard 拦截(打开是拦截页);
 *   CDN/遥测/推送/统计端点(不是给人访问的网站, 403/404/超时)。 */
function domainHref(d: string, blocked?: boolean, kind?: string): string | null {
  if (d.endsWith(".in-addr.arpa") || d.endsWith(".ip6.arpa")) return null;
  if (blocked) return null;
  if (kind === "cdn" || kind === "sdk") return null;
  return "https://" + d;
}

/** 域名不可点时的 hover 说明。 */
function domainNoLinkTitle(blocked: boolean, kind?: string): string | undefined {
  if (blocked) return "被 AdGuard 拦截, 打开也是拦截页";
  if (kind === "sdk") return "数据上报/推送端点, 不是网站, 无法打开";
  if (kind === "cdn") return "CDN/基础设施, 不是网站";
  return undefined;
}

/** 全家域名 Top（条形图，拦截的排前 + 红标）。 */
export function DnsTopList({ top }: { top: DnsDomain[] }): React.ReactElement | null {
  if (!top.length) {
    return <div className="dnsoff">近 1h 无 DNS 查询记录 —— 确认有设备把 DNS 首选指向了 AdGuard（<code>make adguard-up</code> 起的 :53 DNS 口）。</div>;
  }
  const maxTop = Math.max(top[0].count, 1);
  return (
    <div className="dnstop">
      <div className="dnstop-title">全家域名 Top {top.length}<span className="rtrtop-hint">· 拦截的排前</span></div>
      {top.map((t, i) => {
        const s = siteName(t.domain);
        const href = domainHref(t.domain, t.blocked > 0, s?.kind);
        const nt = domainNoLinkTitle(t.blocked > 0, s?.kind);
        return (
          <div key={i} className={"dnstop-row" + (t.blocked > 0 ? " blocked" : "")} title={new Date((t.last_ts || 0) * 1000).toLocaleString("zh-CN")}>
            <span className={"rtrtop-rank" + (i === 0 ? " top" : "")}>{i + 1}</span>
            <span className="dnstop-dom mono">
              {href ? <a href={href} target="_blank" rel="noopener noreferrer">{t.domain}</a>
                : <span title={nt}>{t.domain}</span>}
              {s ? <em className="sitename">{s.name}</em> : null}
            </span>
            <div className="dnstop-bar"><div style={{ width: Math.round((t.count / maxTop) * 100) + "%" }} /></div>
            <span className="dnstop-cnt">{t.count} 次</span>
            {t.blocked > 0 ? <span className="dnstop-blk">拦截 {t.blocked}</span> : null}
          </div>
        );
      })}
    </div>
  );
}

/** 各设备卡片: 每台客户端最近解析的域名(默认 5 条, 可展开看全部)。 */
export function DnsClients({ clients, clientIps, ipNames }: {
  clients: Record<string, DnsClient>;
  clientIps: string[];
  ipNames: Map<string, string>;
}): React.ReactElement | null {
  const [open, setOpen] = React.useState<ReadonlySet<string>>(new Set());
  if (!clientIps.length) return null;
  const toggle = (ip: string) => setOpen((prev) => {
    const n = new Set(prev);
    n.has(ip) ? n.delete(ip) : n.add(ip);
    return n;
  });
  return (
    <div className="dnsclients">
      <div className="dnstop-title">各设备最近解析的域名<span className="rtrtop-hint">· 近 1h · 点卡片可展开全部</span></div>
      {clientIps.map((ip) => {
        const c: DnsClient = clients[ip];
        const nm = ipNames.get(ip) || c.name || ip;
        const all = c.top || [];
        const show = open.has(ip) ? all : all.slice(0, 5);
        return (
          <div key={ip} className={"dnsclient" + (open.has(ip) ? " open" : "")}>
            <div className="dnsclient-head">
              <span className="dnsc-client-name">{nm}</span>
              <span className="mono dnsc-ip">{ip}</span>
              <span className="dnsc-stat">{c.total} 次查询{c.blocked > 0 ? ` · 拦截 ${c.blocked}` : ""}</span>
            </div>
            {all.length > 0 ? (
              <>
                <div className="dnsc-domains">
                  {show.map((t, i) => {
                    const s = siteName(t.domain);
                    const href = domainHref(t.domain, t.blocked > 0, s?.kind);
                    const nt = domainNoLinkTitle(t.blocked > 0, s?.kind);
                    return (
                      <span key={i} className={"dnsc-dom" + (t.blocked > 0 ? " blocked" : "")}>
                        {href ? <a href={href} target="_blank" rel="noopener noreferrer">{t.domain}</a>
                          : <span title={nt}>{t.domain}</span>}
                        {s ? <em className="sitename">{s.name}</em> : null}<em>{t.count}×</em>
                      </span>
                    );
                  })}
                </div>
                {all.length > 5 ? (
                  <button className="btn ghost dnsc-more" onClick={() => toggle(ip)}>
                    {open.has(ip) ? "收起" : `展开全部 ${all.length} 个域名`}
                  </button>
                ) : null}
              </>
            ) : <div className="dnsc-domains dim">近 1h 无域名解析记录</div>}
          </div>
        );
      })}
    </div>
  );
}

/** 薄壳: 标题行 + 组装 Guide / TopList / Clients。 */
export function DnsPanel({ dns, ipNames, hostIp }: {
  dns: DnsPayload;
  ipNames: Map<string, string>;
  hostIp: string;
}): React.ReactElement | null {
  const dnsTarget = hostIp || "192.168.x.x";   // 兜底
  const title = (
    <div className="dnstitle">
      🌐 域名访问
      <span className="rtrtop-hint">· AdGuard DNS 记录</span>
      <a href={ADGUARD_URL} target="_blank" rel="noreferrer" className="dnstitle-link">控制台 ↗</a>
    </div>
  );

  // 未启用(没数据文件 / AdGuard 没起): 只显示标题 + 指引 + 提示
  if (!dns.available) {
    return (
      <div className="dnsbox dim">
        {title}
        <DnsGuide dns={dns} dnsTarget={dnsTarget} />
        <div className="dnsoff">未启用 DNS 记录 —— 照上面步骤配好设备 DNS 后跑 <code>make dns-fetch</code> 即可看到「谁访问了什么域名」。</div>
      </div>
    );
  }

  // 有数据: 标题带统计 + 可达性标记, 再组装三块
  const clients = dns.clients || {};
  const clientIps = Object.keys(clients).sort((a, b) => (clients[b]?.total ?? 0) - (clients[a]?.total ?? 0));
  const top = dns.top_domains || [];
  /* 导出近 1h DNS 记录为 CSV(域名 Top + 各客户端查询量), 与设备列表导出一致。 */
  const exportDnsCsv = () => {
    const esc = (v: unknown) => {
      const s = v === undefined || v === null ? "" : String(v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    const rows: string[][] = [
      ["类型", "名称", "次数", "被拦截"],
      ...top.map((t) => ["域名", t.domain, String(t.count ?? 0), String(t.blocked ?? 0)]),
      ...clientIps.map((ip) => ["客户端", ipNames.get(ip) || ip, String(clients[ip]?.total ?? 0), String(clients[ip]?.blocked ?? 0)]),
    ];
    const blob = new Blob([rows.map((r) => r.map(esc).join(",")).join("\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "lume-lan-dns-" + new Date().toISOString().slice(0, 10) + ".csv";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
  };
  return (
    <div className="dnsbox">
      <div className="dnstitle">
        🌐 域名访问
        <span className="rtrtop-hint">· 近 1h · {dns.query_count ?? 0} 次查询 · {dns.client_count ?? 0} 客户端</span>
        {!dns.reachable ? <span className="dnsstale">⏳ AdGuard 未连接（沿用上次的记录）</span> : null}
        <a href={ADGUARD_URL} target="_blank" rel="noreferrer" className="dnstitle-link">控制台 ↗</a>
        <button className="btn ghost dns-export" onClick={exportDnsCsv} title="导出近 1h DNS 记录为 CSV(域名+客户端)">导出 CSV</button>
      </div>
      <DnsGuide dns={dns} dnsTarget={dnsTarget} />
      <DnsTopList top={top} />
      <DnsClients clients={clients} clientIps={clientIps} ipNames={ipNames} />
    </div>
  );
}
