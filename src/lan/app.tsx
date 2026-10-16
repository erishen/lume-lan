/* Lume LAN — 局域网设备看板。单一 app.js bundle(history 路由 + 挂载)。
 *
 * 数据流:宿主 `make scan`(python3 scripts/scan_lan.py)写 .data/devices.json →
 * Lume 服务端 /api/devices 读快照下发 → 本界面展示。浏览器「刷新」按钮重拉
 * /api/devices(快照由宿主刷新,前端只是重新读)。
 *
 * 路由表:
 *   /   仪表盘(设备列表 + 摘要 KPI + 搜索/在线筛选)
 */
import React from "react";
import { createRoot } from "react-dom/client";
import {
  DevicesPayload,
  RouterPayload,
  RouterDevice,
  RouterTrafficPayload,
  DnsPayload,
  DnsClient,
  DnsDomain,
  apiDevices,
  apiRouter,
  apiRouterTraffic,
  apiDns,
  fmtTs,
  fmtAge,
  fmtBytes,
} from "../api";

function parsePath(): { view: string } {
  const p = window.location.pathname.replace(/\/+$/, "");
  return { view: p.replace(/^\//, "").split("/")[0] || "" };
}

function navigate(href: string): void {
  let url: URL;
  try {
    url = new URL(href, window.location.href);
  } catch {
    return;
  }
  if (url.origin !== window.location.origin) return;
  window.history.pushState(null, "", url.pathname + url.search);
  window.dispatchEvent(new Event("lan:navigate"));
}

/* 近 1h 流量增量阈值: ≥ 1MB 视为「活跃」, 否则「挂起中」(心跳级)。
 * delta_1h 为 null(历史刚建、未知)时, 在线设备默认按活跃显示, 不误伤。 */
function isActive(d: RouterDevice): boolean {
  if (!d.online) return false;
  const delta = d.delta_1h;
  return delta === undefined || delta === null ? true : delta >= 1048576;
}

/* 设备行:IP / 设备名(路由器 HostName/厂商,按 IP 关联) / MAC / 网卡 / 来源 / 在线态。
 * state: 3 态(与终端列表一致)on=在线·活跃 idle=在线·挂起 off=离线; 缺省从 live 推断。 */
function DeviceRow({ ip, mac, iface, source, live, gateway, name, state }: {
  ip: string; mac: string; iface?: string; source?: string; live?: boolean; gateway?: boolean; name?: string;
  state?: "on" | "idle" | "off";
}): React.ReactElement {
  const st = state ?? (live === false ? "off" : "on");
  return (
    <tr className={"row-" + st}>
      <td className="ip">{ip}{gateway ? <span className="tag gw">网关</span> : null}</td>
      <td>{name ? <span className="devname" title={name}>{name}</span> : <span className="dim">—</span>}</td>
      <td className="mono">{mac || <span className="dim">—</span>}</td>
      <td>{iface ? <span className="iface">{iface}</span> : <span className="dim">—</span>}</td>
      <td><span className={"src src-" + (source || "arp")}>{source === "router" ? "路由器" : source === "sweep" ? "Ping" : "ARP"}</span></td>
      <td>
        {st === "off" ? <><span className="dot off" />离线</>
          : st === "idle" ? <><span className="dot idle" />在线·挂起</>
          : <><span className="dot on" />在线</>}
      </td>
    </tr>
  );
}

/* 家庭总流量趋势:纯 div 柱状图(每根柱 = 一个 60s 刷新周期的全家庭流量增量)。
 * 数据来自 router_traffic_total 累计序列的前端差分;历史 <2 点不显示。
 * 峰值柱高亮,并标注「峰值时刻 + 主因设备」(谁在吃带宽)。 */
function TrafficChart({ rows, max, peakIdx, peak }: {
  rows: { ts: number; delta: number; top_name?: string; top_ip?: string }[];
  max: number; peakIdx: number; peak: { ts: number; delta: number; top_name?: string; top_ip?: string };
}): React.ReactElement {
  const spanMin = Math.round((rows[rows.length - 1].ts - rows[0].ts) / 60);
  const sumDelta = rows.reduce((a, r) => a + r.delta, 0);
  const peakTime = new Date(peak.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" });
  const peakCause = peak.top_name || peak.top_ip;
  return (
    <div className="trend">
      <div className="trend-title">
        家庭流量趋势（近 {spanMin} 分钟，每 60s 采样 · 合计 {fmtBytes(sumDelta)}）
      </div>
      {peak.delta > 0 && peakCause ? (
        <div className="trend-peak">
          📈 峰值 <b>{peakTime}</b>（{fmtBytes(peak.delta)}/周期）· 主因 <b>{peakCause}</b>
          {peak.top_ip ? <span className="trend-peak-ip mono">{peak.top_ip}</span> : null}
        </div>
      ) : null}
      <div className="trend-bars">
        {rows.map((r, i) => (
          <div
            key={i}
            className={"trendbar" + (i === peakIdx ? " peak" : "") + (r.delta > 0 ? "" : " zero")}
            title={`${new Date(r.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false })} · ${fmtBytes(r.delta)}${i === peakIdx && r.top_name ? " · 主因 " + r.top_name : ""}`}
            style={{ height: Math.max(2, Math.round((r.delta / max) * 100)) + "%" }}
          />
        ))}
      </div>
      <div className="trend-axis">
        <span>{new Date(rows[0].ts * 1000).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" })}</span>
        <span>柱 = 该周期全家庭流量（↑↓ 合计）</span>
        <span>{new Date(rows[rows.length - 1].ts * 1000).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" })}</span>
      </div>
    </div>
  );
}

/* 信号强度柱(3 段, 按 rssi 0~100 映射 good/mid/bad; 有线/未知显示 —)。
 * rssi 来自 device/topology(HostInfo 的 RSSI 恒 0)。 */
function Rssi({ rssi, wired, linkQuality }: { rssi?: number | null; wired?: boolean | null; linkQuality?: string }): React.ReactElement {
  if (wired) {
    return <span className="rssi wired" title="有线接入" />;
  }
  if (rssi === undefined || rssi === null) {
    return <span className="dim" title="未知接入方式">—</span>;
  }
  let cls = "good";
  if (rssi < 30) cls = "bad";
  else if (rssi < 50) cls = "mid";
  const on = cls === "good" ? 3 : cls === "mid" ? 2 : 1;
  const lq = linkQuality ? ` · ${linkQuality}` : "";
  return (
    <span className={"rssi " + cls} title={`信号 ${rssi}${lq}`}>
      {[1, 2, 3].map((i) => <i key={i} style={i <= on ? {} : undefined} />)}
    </span>
  );
}

/* WAN 状态卡: 默认/主连接的状态(连接态/接入方式/IPv4/IPv6/DNS/NAT 类型/带宽峰值)。 */
function WanCard({ wan }: { wan: RouterPayload["router_wan"] }): React.ReactElement | null {
  if (!wan || !wan.status) return null;
  const up = wan.status === "Up" || wan.connection === "Connected";
  const mbps = (k?: number) => (k && k > 0 ? (k / 1000).toFixed(1) + " Mbps" : "—");
  const nowD = wan.down_now_kbps, nowU = wan.up_now_kbps;
  const hasNow = (nowD || 0) > 0 || (nowU || 0) > 0;
  return (
    <div className="wancard">
      <div className="wan-title">WAN 状态（{wan.access_type || "以太网"}接入）</div>
      <span className={"wan-status" + (up ? "" : " down")}>{up ? "● 已连接" : "○ 断开"}</span>
      <div className="wan-grid">
        <span className="wan-cell">IPv4 <b>{wan.ipv4_addr || "—"}</b>{wan.ipv4_addr_type ? " · " + wan.ipv4_addr_type : ""}</span>
        <span className="wan-cell">网关 <b>{wan.ipv4_gateway || "—"}</b></span>
        <span className="wan-cell">DNS <b>{wan.dns || "—"}</b></span>
        {wan.ipv6_addr ? <span className="wan-cell">IPv6 <b>{wan.ipv6_addr.split("/")[0]}</b></span> : null}
        {wan.nat_type ? <span className="wan-cell">NAT 类型 <b>{wan.nat_type}</b></span> : null}
        <span className="wan-cell" title="当前速率(*BandwidthHistory 最近一个采样点) / 历史峰值(*BandwidthMax, 路由器累计最大)">
          当前 ↓<b>{hasNow ? mbps(nowD) : "—"}</b> · 峰值 ↓<b>{mbps(wan.down_max_kbps)}</b>
        </span>
      </div>
    </div>
  );
}

/* 单设备流量趋势:纯 div 柱状图(每根柱 = 该设备一个 60s 周期的流量增量,↑↓ 合计)。
 * 数据来自 /api/router-traffic 的该设备累计序列, 前端差分; 历史 <2 点不显示。
 * 头部标注设备名/IP + 窗口内 下载/上传/合计, 点「×」收起。 */
function PerDeviceChart({ device, rows, max, win }: {
  device: { name: string; ip: string };
  rows: { ts: number; delta: number; up: number; down: number }[];
  max: number;
  win: { rx: number; tx: number; spanMin: number };
}): React.ReactElement {
  const peak = rows.reduce((a, r) => (r.delta > a.delta ? r : a), rows[0]);
  const peakTime = new Date(peak.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" });
  return (
    <div className="trend perdev">
      <div className="trend-title">
        {device.name ? device.name + " " : null}<span className="trend-peak-ip mono">{device.ip}</span>
        <span className="perdev-sub">· 近 {win.spanMin} 分钟 · ↓{fmtBytes(win.rx)} ↑{fmtBytes(win.tx)}</span>
      </div>
      <div className="trend-bars">
        {rows.map((r, i) => (
          <div
            key={i}
            className={"trendbar" + (i === rows.length - 1 ? " last" : "") + (r.delta > 0 ? "" : " zero")}
            title={`${new Date(r.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false })} · ↓${fmtBytes(r.down)} ↑${fmtBytes(r.up)}`}
            style={{ height: Math.max(2, Math.round((r.delta / max) * 100)) + "%" }}
          />
        ))}
      </div>
      <div className="trend-axis">
        <span>{new Date(rows[0].ts * 1000).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" })}</span>
        <span>柱 = 该设备该周期流量（↑↓ 合计）· 峰值 {peakTime} {fmtBytes(peak.delta)}</span>
        <span>{new Date(rows[rows.length - 1].ts * 1000).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" })}</span>
      </div>
    </div>
  );
}

/* 单设备详情卡: 点选设备时展示它的全部字段(MAC/厂商/类型/频段/信号/接入方式/
 * 在线态/最后活跃/累计流量/近 1h 增量/平均速率)。曲线没有历史时这里兜底,
 * 保证「点一下总能看到这台设备的具体情况」。 */
function DeviceDetail({ d, name }: { d: RouterDevice; name: string }): React.ReactElement {
  const typeLabel: Record<string, string> = { mobile: "手机", laptop: "笔记本", computer: "电脑", television: "电视", tablet: "平板", android: "Android" };
  const cells: [string, React.ReactNode][] = [
    ["设备名", name || d.mac],
    ["MAC", <span className="mono">{d.mac || "—"}</span>],
    ["厂商", d.vendor || "—"],
    ["类型", d.icon ? (typeLabel[d.icon] || d.icon) : "—"],
    ["频段", d.freq || "—"],
    ["信号", d.access_type === "Wireless" ? <Rssi rssi={d.rssi} linkQuality={d.link_quality} /> : d.access_type === "Ethernet" ? "有线" : "—"],
    ["最后活跃", d.last_seen ? <span className="mono">{d.last_seen.slice(5, 16)}</span> : "—"],
    ["累计 下载/上传", <span className="mono">↓{fmtBytes(d.rx_bytes)} · ↑{fmtBytes(d.tx_bytes)}</span>],
    ["近 1h 增量", d.delta_1h !== undefined && d.delta_1h !== null ? <span className="mono">{fmtBytes(d.delta_1h)}（≈{fmtBytes(Math.round(d.delta_1h / 3600))}/s）</span> : "—"],
    ["状态", d.online ? <span className="det-on">{d.delta_1h !== null && d.delta_1h !== undefined && d.delta_1h >= 1048576 ? "活跃" : "挂起中"}</span> : <span className="det-off">离线</span>],
  ];
  return (
    <div className="devdetail">
      <div className="dd-title">设备详情 · {name || d.ip}</div>
      <div className="dd-grid">
        {cells.map(([k, v], i) => (
          <div key={i} className="dd-cell"><span className="dd-k">{k}</span><span className="dd-v">{v}</span></div>
        ))}
      </div>
    </div>
  );
}

/* DNS 域名访问区(AdGuard Home 查询日志聚合):
 *  ① 全家域名排行 Top10(近 1h, 拦截的排前)
 *  ② 每客户端 IP → 最近解析的域名 Top5(哪个设备在看什么)。
 * reachable=false → AdGuard 没连上, 显示降级横幅(旧数据若有仍列)。
 * 设备 IP 显示名与主表一致(用 ipNames)。 */
function DnsPanel({ dns, ipNames }: {
  dns: DnsPayload;
  ipNames: Map<string, string>;
}): React.ReactElement | null {
  if (!dns.available) {
    return (
      <div className="dnsbox dim">
        <div className="dnstitle">🌐 域名访问<span className="rtrtop-hint">· AdGuard DNS 记录</span></div>
        <div className="dnsoff">未启用 DNS 记录 —— 起 AdGuard（<code>make adguard-up</code>）+ 配设备 DNS 后跑 <code>make dns-fetch</code> 即可看到「谁访问了什么域名」。</div>
      </div>
    );
  }
  const clients = dns.clients || {};
  const clientIps = Object.keys(clients).sort((a, b) => (clients[b]?.total ?? 0) - (clients[a]?.total ?? 0));
  const top = dns.top_domains || [];
  const maxTop = top.length ? Math.max(top[0].count, 1) : 1;
  return (
    <div className="dnsbox">
      <div className="dnstitle">
        🌐 域名访问
        <span className="rtrtop-hint">· 近 1h · {dns.query_count ?? 0} 次查询 · {dns.client_count ?? 0} 客户端</span>
        {!dns.reachable ? <span className="dnsstale">⏳ AdGuard 未连接（沿用上次的记录）</span> : null}
      </div>

      {top.length > 0 ? (
        <div className="dnstop">
          <div className="dnstop-title">全家域名 Top {top.length}<span className="rtrtop-hint">· 拦截的排前</span></div>
          {top.map((t, i) => (
            <div key={i} className={"dnstop-row" + (t.blocked > 0 ? " blocked" : "")} title={new Date((t.last_ts || 0) * 1000).toLocaleString("zh-CN")}>
              <span className={"rtrtop-rank" + (i === 0 ? " top" : "")}>{i + 1}</span>
              <span className="dnstop-dom mono">{t.domain}</span>
              <div className="dnstop-bar"><div style={{ width: Math.round((t.count / maxTop) * 100) + "%" }} /></div>
              <span className="dnstop-cnt">{t.count} 次</span>
              {t.blocked > 0 ? <span className="dnstop-blk">拦截 {t.blocked}</span> : null}
            </div>
          ))}
        </div>
      ) : <div className="dnsoff">近 1h 无 DNS 查询记录 —— 确认有设备把 DNS 首选指向了 AdGuard（本机会话：<code>make adguard-up</code> 起的 3000/53 端口）。</div>}

      {clientIps.length > 0 ? (
        <div className="dnsclients">
          <div className="dnstop-title">各设备最近解析的域名<span className="rtrtop-hint">· 近 1h</span></div>
          {clientIps.map((ip) => {
            const c: DnsClient = clients[ip];
            const nm = ipNames.get(ip) || c.name || ip;
            return (
              <div key={ip} className="dnsclient">
                <div className="dnsclient-head">
                  <span className="dnsc-client-name">{nm}</span>
                  <span className="mono dnsc-ip">{ip}</span>
                  <span className="dnsc-stat">{c.total} 次查询{c.blocked > 0 ? ` · 拦截 ${c.blocked}` : ""}</span>
                </div>
                {c.top && c.top.length > 0 ? (
                  <div className="dnsc-domains">
                    {c.top.map((t, i) => (
                      <span key={i} className={"dnsc-dom" + (t.blocked > 0 ? " blocked" : "")}>
                        {t.domain}<em>{t.count}×</em>
                      </span>
                    ))}
                  </div>
                ) : <div className="dnsc-domains dim">近 1h 无域名解析记录</div>}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function Dashboard(): React.ReactElement {
  const [data, setData] = React.useState<DevicesPayload | null>(null);
  const [router, setRouter] = React.useState<RouterPayload | null>(null);
  const [err, setErr] = React.useState<string>("");
  const [kw, setKw] = React.useState("");
  const [onlyLive, setOnlyLive] = React.useState(false);
  const [typeFilter, setTypeFilter] = React.useState("");
  const [freqFilter, setFreqFilter] = React.useState("");
  const [lastFetch, setLastFetch] = React.useState<number>(0);
  /* 单设备流量趋势: 点选某台设备(Top5/终端行)看它近 5h 自己的曲线 */
  const [selIp, setSelIp] = React.useState<string>("");
  const [rtrTraffic, setRtrTraffic] = React.useState<RouterTrafficPayload | null>(null);
  const [dns, setDns] = React.useState<DnsPayload | null>(null);

  const load = React.useCallback(async () => {
    setErr("");
    try {
      const [d, ro, rt, dn] = await Promise.all([
        apiDevices(),
        apiRouter().catch(() => null),
        apiRouterTraffic().catch(() => null),
        apiDns().catch(() => null),
      ]);
      setData(d);
      setRouter(ro);
      setRtrTraffic(rt);
      setDns(dn);
      setLastFetch(Date.now());
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  /* IP → 显示名。优先级: 本地别名(.data/aliases.json, 最高, 不依赖登录态)
   * > 路由器备注/设备名(hostname 由宿主 extract 时已按 ActualName>HostName 取过)
   * > 厂商(vendor)。本地别名让你在路由器没改好名/登录态拉不到时也能自定义。 */
  const ipNames = React.useMemo(() => {
    const m = new Map<string, string>();
    const aliases = router?.router_aliases || {};
    if (router?.router_devices) {
      for (const d of router.router_devices) {
        if (!d.ip) continue;
        const label = aliases[d.ip] || d.hostname || d.vendor || "";
        if (label) m.set(d.ip, label);
      }
    }
    /* 本地别名即使设备不在当前终端列表里也要能显示(如刚改名尚未下发) */
    for (const [ip, name] of Object.entries(aliases)) {
      if (name) m.set(ip, name);
    }
    return m;
  }, [router]);

  /* IP → 设备类型(路由器 icon 原始键) / Wi-Fi 频段。供 chip 点击筛选。 */
  const ipType = React.useMemo(() => {
    const m = new Map<string, string>();
    router?.router_devices?.forEach((d) => { if (d.ip && d.icon) m.set(d.ip, d.icon); });
    return m;
  }, [router]);
  const ipFreq = React.useMemo(() => {
    const m = new Map<string, string>();
    router?.router_devices?.forEach((d) => { if (d.ip && d.freq) m.set(d.ip, d.freq); });
    return m;
  }, [router]);
  /* IP → 三态(与终端列表同语义):路由器端有 delta_1h 时按 1MB 阈值分「在线·挂起/在线」,
   * 没有的(本机 ARP 快照)兜底为 online 布尔。 */
  const ipState = React.useMemo(() => {
    const m = new Map<string, "on" | "idle" | "off">();
    const seenIps = new Set<string>();
    data?.devices?.forEach((d) => { if (d.ip) { m.set(d.ip, d.live ? "on" : "off"); seenIps.add(d.ip); } });
    router?.router_devices?.forEach((d) => {
      if (!d.ip || seenIps.has(d.ip)) return;
      m.set(d.ip, !d.online ? "off" : isActive(d) ? "on" : "idle");
    });
    return m;
  }, [data, router]);

  /* 合并列表:ARP 本机快照 + 路由器在线设备(不在 ARP 表里的补进来, source=router)。
   * 这样「在线设备」不再受限于本机 arp 缓存,而是全网在线都可见。 */
  const shown = React.useMemo(() => {
    if (!data) return [];
    const list = [...data.devices];
    /* 只要路由器有终端数据(不管登录态是新鲜还是 stale/冷却)就合并。 */
    const rdevs = router?.router_devices;
    if (rdevs && rdevs.length > 0) {
      const arpIps = new Set(list.map((d) => d.ip));
      for (const rd of rdevs) {
        if (!rd.online || !rd.ip || arpIps.has(rd.ip)) continue;
        list.push({
          ip: rd.ip,
          mac: rd.mac || "",
          live: true,
          source: "router",
          iface: "",
          gateway: data.gateway ? rd.ip === data.gateway : undefined,
        });
      }
    }
    const k = kw.trim().toLowerCase();
    return list.filter((d) => {
      if (onlyLive && d.live === false) return false;
      /* 类型/频段筛选(来自路由器 HostInfo, 独立 AND 条件, 可多选同组内互斥):
       * 本机 ARP 设备无 icon/freq 元数据, 选中筛选时自然被排除(符合语义)。 */
      if (typeFilter && ipType.get(d.ip) !== typeFilter) return false;
      if (freqFilter && ipFreq.get(d.ip) !== freqFilter) return false;
      if (!k) return true;
      const name = (ipNames.get(d.ip) || "").toLowerCase();
      return (
        d.ip.toLowerCase().includes(k) ||
        (d.mac || "").toLowerCase().includes(k) ||
        (d.iface || "").toLowerCase().includes(k) ||
        name.includes(k)
      );
    });
  }, [data, router, kw, onlyLive, typeFilter, freqFilter, ipNames, ipType, ipFreq]);

  /* KPI:路由器有终端数据时(不管新鲜/stale/冷却)用全网口径(32 在册/8 在线),
   * 否则退回 ARP 本机口径。data 未加载(初值 null)时给 0,避免读 null.count 崩溃。 */
  const hasRtrData = !!(router?.router_devices && router.router_devices.length > 0);
  const deviceTotal = !data ? 0 : hasRtrData ? (router.router_count ?? data.count) : data.count;
  const onlineTotal = !data ? 0 : hasRtrData ? (router.router_online ?? data.live_count) : data.live_count;

  /* 路由器终端流量 Top 5(按累计下载排序,数据已按该序下发)。 */
  const top5 = React.useMemo(() => {
    if (!router || !router.router_devices) return [];
    const list = [...router.router_devices].sort((a, b) => (b.rx_bytes ?? 0) - (a.rx_bytes ?? 0));
    const max = Math.max(1, list[0]?.rx_bytes ?? 1);
    return list.slice(0, 5).map((d) => ({ d, pct: Math.round(((d.rx_bytes ?? 0) / max) * 100) }));
  }, [router]);

  /* 设备类型分布(路由器 IconType: mobile/laptop/computer/television/tablet/android)。
   * 保留原始 icon 键, 供 chip 点击筛选时与 ipType 匹配。 */
  const typeDist = React.useMemo(() => {
    const labelOf: Record<string, string> = {
      mobile: "手机", laptop: "笔记本", computer: "电脑",
      television: "电视", tablet: "平板", android: "Android",
    };
    const m = new Map<string, number>();
    router?.router_devices?.forEach((d) => {
      if (d.icon) m.set(d.icon, (m.get(d.icon) || 0) + 1);
    });
    return [...m.entries()]
      .map(([k, n]) => ({ key: k, label: labelOf[k] || k, n }))
      .sort((a, b) => b.n - a.n);
  }, [router]);

  /* Wi-Fi 频段分布(5GHz / 2.4GHz)。 */
  const freqDist = React.useMemo(() => {
    const m = new Map<string, number>();
    router?.router_devices?.forEach((d) => {
      if (d.freq) m.set(d.freq, (m.get(d.freq) || 0) + 1);
    });
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [router]);

  /* 家庭总流量趋势:router_traffic_total 是累计值序列,相邻点差值 = 该刷新周期的流量。
   * 取最近 180 点(60s 循环 ≈ 3 小时)。每点带主因设备(宿主算好的 top_* 字段)。 */
  const trafficTrend = React.useMemo(() => {
    const s = router?.router_traffic_total;
    if (!s || s.length < 2) return null;
    const pts = s.slice(-180);
    let max = 1;
    let peakIdx = 0;
    const rows = pts.map((p, i) => {
      const delta = i === 0 ? 0 : Math.max(0, p.rx - pts[i - 1].rx + p.tx - pts[i - 1].tx);
      if (delta > max) { max = delta; peakIdx = i; }
      return {
        ts: p.ts, delta,
        top_name: p.top_name || "", top_ip: p.top_ip || "",
      };
    });
    const peak = rows[peakIdx];
    return { rows, max, peakIdx, peak };
  }, [router]);

  /* 异常流量告警: 在线设备近 1h 增量 ≥ ANOMALY_BYTES(默认 10GB)就提示, 帮发现"谁在猛传"。 */
  const ANOMALY_BYTES = 10 * 1024 * 1024 * 1024;
  const anomalies = React.useMemo(() => {
    const out: { ip: string; name: string; d: number }[] = [];
    router?.router_devices?.forEach((d) => {
      if (!d.online || d.delta_1h === undefined || d.delta_1h === null) return;
      if (d.delta_1h >= ANOMALY_BYTES) out.push({ ip: d.ip, name: d.hostname || d.vendor || d.mac, d: d.delta_1h });
    });
    return out.sort((a, b) => b.d - a.d);
  }, [router]);

  /* 导出当前列表为 CSV(32 台终端 + 流量 + 备注 + 信号), 供存档。 */
  const exportCsv = React.useCallback(() => {
    const rows = router?.router_devices || [];
    const head = ["ip", "name", "vendor", "mac", "online", "active", "freq", "type", "rssi", "link_quality", "access", "rx_bytes", "tx_bytes", "delta_1h_bytes", "last_seen"];
    const esc = (v: unknown) => {
      const s = v === undefined || v === null ? "" : String(v);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    const lines = [head.join(",")];
    for (const d of rows) {
      lines.push([
        d.ip, ipNames.get(d.ip) || d.hostname || "", d.vendor, d.mac,
        d.online ? 1 : 0, d.online && isActive(d) ? 1 : 0,
        d.freq, d.icon, d.rssi ?? "", d.link_quality, d.access_type,
        d.rx_bytes ?? 0, d.tx_bytes ?? 0, d.delta_1h ?? "", d.last_seen,
      ].map(esc).join(","));
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "lume-lan-devices-" + new Date().toISOString().slice(0, 10) + ".csv";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
  }, [router, ipNames]);

  /* 选中设备(Top5/终端行点选)的近 5h 流量曲线: 取 rtrTraffic.series[selIp] 累计序列,
   * 前端差分成每 60s 周期的 ↓/↑ 增量; <2 点无基线则 null。 */
  const selChart = React.useMemo(() => {
    if (!selIp || !rtrTraffic?.series) return null;
    const s = rtrTraffic.series[selIp];
    if (!s || s.length < 2) return null;
    let max = 1;
    let winRx = 0, winTx = 0;
    const rows = s.map((p, i) => {
      const down = i === 0 ? 0 : Math.max(0, p.rx - s[i - 1].rx);
      const up = i === 0 ? 0 : Math.max(0, p.tx - s[i - 1].tx);
      const delta = down + up;
      if (delta > max) max = delta;
      winRx += down; winTx += up;
      return { ts: p.ts, delta, up, down };
    });
    const spanMin = Math.round((s[s.length - 1].ts - s[0].ts) / 60);
    const dev = router?.router_devices?.find((d) => d.ip === selIp);
    return {
      device: { name: ipNames.get(selIp) || dev?.hostname || dev?.vendor || "", ip: selIp },
      rows, max,
      win: { rx: winRx, tx: winTx, spanMin },
    };
  }, [selIp, rtrTraffic, router, ipNames]);
  /* 点选设备(Top5/终端行)选中它的流量曲线。
   * 单击: 延迟 260ms 后 toggle; 双击(260ms 内两次): 取消待执行的 toggle, 直接选中。
   * 这样单击、双击都能选中(双击不再"选中又取消"净消), 点在别的设备上再点才取消。 */
  const clickTimer = React.useRef<{ t: ReturnType<typeof setTimeout>; ip: string } | null>(null);
  const selectDevice = React.useCallback((ip: string) => {
    if (clickTimer.current) {
      clearTimeout(clickTimer.current.t);
      if (clickTimer.current.ip === ip) {
        clickTimer.current = null;
        setSelIp(ip);               // 双击同设备: 直接选中(不 toggle 掉)
        return;
      }
      clickTimer.current = null;    // 双击了不同设备: 按单击流程处理新的
    }
    clickTimer.current = {
      ip,
      t: setTimeout(() => {
        clickTimer.current = null;
        setSelIp((cur) => (cur === ip ? "" : ip));
      }, 260),
    };
  }, []);

  /* 选中设备的详情(详情卡用, 来自 router_devices 全字段)。 */
  const selDevice = React.useMemo(() => {
    if (!selIp) return null;
    return router?.router_devices?.find((d) => d.ip === selIp) || null;
  }, [selIp, router]);
  const [showAll, setShowAll] = React.useState(false);

  return (
    <section className="dash">
      {data && !data.missing ? (
        <>
          <div className="kpis">
            <div className="kpi"><div className="k">设备</div><div className="v">{deviceTotal}</div><div className="u">{hasRtrData ? "台在册(全网)" : "台在册(本机)"}</div></div>
            <div className="kpi ok"><div className="k">在线</div><div className="v">{onlineTotal}</div><div className="u">{hasRtrData ? "live=true(全网)" : "live=true(本机)"}</div></div>
            <div className="kpi"><div className="k">网关</div><div className="v small">{data.gateway || "—"}</div><div className="u">{data.subnet}</div></div>
            <div className="kpi"><div className="k">快照</div><div className="v small">{fmtAge(data.scanned_at)}</div><div className="u">{data.sweep ? "含 ping sweep" : "仅 arp 表"}</div></div>
          </div>

          {router && router.available ? (
            <div className="routerbox">
              <div className="routerhead">
                <span className="routermodel">{router.friendly || router.model || "路由器"}</span>
                {router.model ? <span className="routermeta">{router.model}</span> : null}
                {router.serial ? <span className="routermeta mono">SN {router.serial}</span> : null}
                {router.manufacturer_oui ? <span className="routermeta">OUI {router.manufacturer_oui}</span> : null}
                {(router.router_count ?? 0) > 0 ? <span className="routermeta">· {router.router_count} 台终端，在线 {router.router_online ?? "—"}{typeof router.router_active === "number" ? `（活跃 ${router.router_active}）` : ""}</span> : null}
              </div>
              {data.gateway ? (
                <div className="quicklinks">
                  <a className="qlink" href={"http://" + data.gateway + "/html/index.html"} target="_blank" rel="noreferrer" data-external="1">🛜 路由器管理页 <span className="qlink-mono">{data.gateway}</span></a>
                  <a className="qlink" href="/mindmap.html" data-external="1">🗺️ 项目导图</a>
                </div>
              ) : null}
              {router.login_stale && (router.router_count ?? 0) > 0 ? (
                <div className="rtrstale">
                  ⏳ {router.login_stale_note || "本次登录失败/冷却中，以下沿用上次的终端数据"}（自动循环稍后重试，无需操作）
                </div>
              ) : null}
              <WanCard wan={router.router_wan} />
              {anomalies.length > 0 ? (
                <div className="alert">
                  ⚠️ {anomalies.length} 台设备近 1h 流量异常偏高（≥10GB）：
                  {anomalies.map((a, i) => (
                    <React.Fragment key={a.ip}>
                      {i > 0 ? "、" : null}
                      <b title={a.ip}>
                        {a.name}（{fmtBytes(a.d)}）
                      </b>
                    </React.Fragment>
                  ))}
                </div>
              ) : null}
              {trafficTrend ? <TrafficChart rows={trafficTrend.rows} max={trafficTrend.max} peakIdx={trafficTrend.peakIdx} peak={trafficTrend.peak} /> : null}
              {(typeDist.length > 0 || freqDist.length > 0) ? (
                <div className="rtrdist">
                  {typeDist.length > 0 ? (
                    <div className="rtrdist-row">
                      <span className="rtrdist-label">设备类型</span>
                      {typeDist.map((t) => (
                        <button
                          key={t.key}
                          type="button"
                          className={"rtrdist-chip rtrdist-btn" + (typeFilter === t.key ? " sel" : "")}
                          onClick={() => setTypeFilter(typeFilter === t.key ? "" : t.key)}
                          title={typeFilter === t.key ? "取消筛选" : "只看" + t.label}
                        >{t.label} {t.n}</button>
                      ))}
                    </div>
                  ) : null}
                  {freqDist.length > 0 ? (
                    <div className="rtrdist-row">
                      <span className="rtrdist-label">Wi-Fi 频段</span>
                      {freqDist.map(([f, n]) => (
                        <button
                          key={f}
                          type="button"
                          className={"rtrdist-chip rtrdist-btn" + (f.indexOf("5G") === 0 ? " g5" : " g24") + (freqFilter === f ? " sel" : "")}
                          onClick={() => setFreqFilter(freqFilter === f ? "" : f)}
                          title={freqFilter === f ? "取消筛选" : "只看" + f}
                        >{f} {n}</button>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
              {top5.length > 0 ? (
                <div className="rtrtop">
                  <div className="rtrtop-title">流量排行（累计下载 Top {top5.length}）<span className="rtrtop-hint">· 点选看该设备流量曲线</span></div>
                  {top5.map((item, i) => (
                    <div
                      key={item.d.ip + item.d.mac}
                      className={"rtrtop-row" + (selIp === item.d.ip ? " sel" : " click")}
                      onClick={() => selectDevice(item.d.ip)}
                      role="button"
                      tabIndex={0}
                    >
                      <span className={"rtrtop-rank" + (i === 0 ? " top" : "")}>{i + 1}</span>
                      <span className="rtrtop-name">{item.d.hostname || item.d.mac}</span>
                      <span className="rtrtop-ip mono">{item.d.ip}</span>
                      {item.d.vendor ? <span className="rtrtop-vendor">{item.d.vendor}</span> : null}
                      {item.d.online ? <span className={"rtrtop-live" + (isActive(item.d) ? "" : " idle")}>{isActive(item.d) ? "活跃" : "挂起"}</span> : null}
                      <div className="rtrtop-bar"><div style={{ width: item.pct + "%" }} /></div>
                      <span className="rtrtop-net">↓{fmtBytes(item.d.rx_bytes)} · ↑{fmtBytes(item.d.tx_bytes)}</span>
                      {item.d.delta_1h !== undefined && item.d.delta_1h !== null ? (
                        <span className="rtrtop-rate" title="近 1h 平均速率（增量/3600）">
                          {fmtBytes(Math.round(item.d.delta_1h / 3600))}/s
                        </span>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : null}
              {selDevice ? (
                <DeviceDetail d={selDevice} name={ipNames.get(selDevice.ip) || selDevice.hostname || selDevice.vendor || ""} />
              ) : null}
              {selChart ? (
                <PerDeviceChart device={selChart.device} rows={selChart.rows} max={selChart.max} win={selChart.win} />
              ) : null}
              {selIp && selDevice && !selChart ? (
                <div className="routernote dim">该设备暂无流量历史（数据循环刚跑或该设备离线已久），点「刷新」或等 1 个周期再看曲线；上方详情卡已展示它的全部信息。</div>
              ) : null}
              {router.router_devices && router.router_devices.length > 0 ? (
                <div className="rtrdevs">
                  {(() => {
                    /* 默认只显示活跃设备; 挂起中/离线的折叠成一行, 可展开 */
                    const active = router.router_devices!.filter((d) => isActive(d));
                    const inactive = router.router_devices!.filter((d) => !isActive(d));
                    const shownList = showAll ? router.router_devices! : active;
                    return (
                    <>
                    {shownList.map((d) => {
                      const st = !d.online ? "off" : isActive(d) ? "on" : "idle";
                      return (
                    <div
                      key={d.ip + d.mac}
                      className={"rtrdev " + (d.online ? "" : "off") + (selIp === d.ip ? " sel" : " click")}
                      onClick={() => selectDevice(d.ip)}
                      role="button"
                      tabIndex={0}
                    >
                      <span className="rtrdev-ip mono">{d.ip}</span>
                      <span className="rtrdev-host">{d.hostname || d.mac}</span>
                      {d.vendor ? <span className="rtrdev-vendor">{d.vendor}</span> : null}
                      <Rssi rssi={d.rssi} wired={d.wired} linkQuality={d.link_quality} />
                      {d.guest ? <span className="rtrdev-tag">访客</span> : null}
                      <span className="rtrdev-net">↓{fmtBytes(d.rx_bytes)} · ↑{fmtBytes(d.tx_bytes)}</span>
                      {d.last_seen ? <span className="rtrdev-last">{d.last_seen.slice(5)}</span> : null}
                      <span className={"rtrdev-state st-" + st}>
                        {st === "off" ? "离线" : st === "idle" ? "挂起中" : "活跃"}
                      </span>
                      <span className={"rtrdev-dot " + st} />
                    </div>
                    );
                    })}
                    {!showAll && inactive.length > 0 ? (
                      <button className="rtrdevs-toggle" onClick={() => setShowAll(true)}>
                        显示挂起中/离线的 {inactive.length} 台
                      </button>
                    ) : null}
                    {showAll && inactive.length > 0 ? (
                      <button className="rtrdevs-toggle" onClick={() => setShowAll(false)}>
                        收起挂起中/离线（默认只显示活跃 {active.length} 台）
                      </button>
                    ) : null}
                    </>
                    );
                  })()}
                </div>
              ) : (
                <div className="routernote">
                  {router.login_stale_note
                    ? "⏳ " + router.login_stale_note + "（自动循环稍后重试，无需操作）"
                    : <>登录态终端列表未取到：{router.login_error ? "路由器登录失败" : "无数据"}。确认 <code>.env</code> 的 <code>ROUTER_PASS</code> 正确后重跑 <code>make router-fetch</code>。</>}
                </div>
              )}
            </div>
          ) : router && router.note ? (
            <div className="routernote dim">{router.note}</div>
          ) : null}

          {dns ? <DnsPanel dns={dns} ipNames={ipNames} /> : null}

          {(data.stale || data.note) ? <div className={"banner " + (data.stale ? "warn" : "")}>{data.note}</div> : null}
          <div className="toolbar">
            <input
              value={kw}
              onChange={(e) => setKw(e.target.value)}
              placeholder="搜 IP / 设备名 / MAC / 网卡…"
            />
            <label className="chk"><input type="checkbox" checked={onlyLive} onChange={(e) => setOnlyLive(e.target.checked)} />只看在线</label>
            {(typeFilter || freqFilter) ? (
              <button
                type="button"
                className="filter-active"
                onClick={() => { setTypeFilter(""); setFreqFilter(""); }}
                title="清除筛选"
              >
                {typeFilter ? ("类型:" + (typeDist.find((t) => t.key === typeFilter)?.label || typeFilter)) : ""}
                {typeFilter && freqFilter ? " · " : ""}
                {freqFilter ? ("频段:" + freqFilter) : ""}
                <span className="filter-clear">✕</span>
              </button>
            ) : null}
            <span className="spacer" />
            <span className="hint">快照于 {fmtTs(data.scanned_at)} · 宿主 <code>make scan</code> 刷新数据源</span>
            {router?.router_devices && router.router_devices.length > 0 ? (
              <button className="btn ghost" onClick={exportCsv} title="导出当前终端列表为 CSV(IP/名称/厂商/流量/信号/最后活跃)">导出 CSV</button>
            ) : null}
            <button className="btn" onClick={() => void load()}>刷新</button>
          </div>
          <div className="tablewrap">
            <table className="devs">
              <thead><tr><th>IP</th><th>设备名</th><th>MAC</th><th>网卡</th><th>来源</th><th>状态</th></tr></thead>
              <tbody>
                {shown.map((d) => (
                  <DeviceRow key={d.ip} ip={d.ip} mac={d.mac} iface={d.iface} source={d.source} live={d.live} gateway={d.gateway} name={ipNames.get(d.ip)} state={ipState.get(d.ip)} />
                ))}
                {shown.length === 0 && <tr><td colSpan={6} className="empty">无匹配设备</td></tr>}
              </tbody>
            </table>
          </div>
        </>
      ) : data && data.missing ? (
        <div className="empty-box">
          <p>暂无设备快照。</p>
          <p className="dim">{data.note}</p>
          <p>在宿主终端跑 <code>make scan</code>(或 <code>python3 scripts/scan_lan.py</code>),然后点「刷新」。</p>
          <button className="btn" onClick={() => void load()}>刷新</button>
        </div>
      ) : (
        <div className="loading">{err ? "加载失败:" + err : "loading…"}</div>
      )}
      {lastFetch ? <div className="fetchline">界面于 {new Date(lastFetch).toLocaleTimeString("zh-CN", { hour12: false })} 读取</div> : null}
    </section>
  );
}

function App(): React.ReactElement {
  const [route] = React.useState(parsePath);
  React.useEffect(() => {
    const onNav = () => { /* 单页,仅重渲;占位供后续扩展路由 */ window.dispatchEvent(new Event("lan:redraw")); };
    window.addEventListener("lan:navigate", onNav);
    document.addEventListener("click", (e) => {
      const a = (e.target as HTMLElement).closest("a");
      if (a && a.origin === window.location.origin && !a.dataset.external) {
        e.preventDefault();
        navigate(a.getAttribute("href") || a.pathname);
      }
    });
    return () => window.removeEventListener("lan:navigate", onNav);
  }, []);
  const view = route.view;
  return (
    <div className="shell">
      <nav className="top">
        <span className="brand">Lume LAN</span>
        <a href="/" className="active">仪表盘</a>
        <a className="home-link" href="https://erishen.cn/" data-external="1">← erishen.cn</a>
      </nav>
      {view === "" || view === "index" ? <Dashboard /> : <div className="empty-box">未知页 <code>/{view}</code></div>}
      <footer className="foot">
        Powered by <a href="https://github.com/erishen/lume" data-external="1" target="_blank" rel="noreferrer">Lume</a> · 一个 .lume 脚本撑起的后台
      </footer>
    </div>
  );
}

const rootEl = document.getElementById("root");
if (rootEl) {
  createRoot(rootEl).render(<App />);
}
