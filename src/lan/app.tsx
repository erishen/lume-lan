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
  apiDevices,
  apiRouter,
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

/* 设备行:IP / 设备名(路由器 HostName/厂商,按 IP 关联) / MAC / 网卡 / 来源 / 在线态。 */
function DeviceRow({ ip, mac, iface, source, live, gateway, name }: {
  ip: string; mac: string; iface?: string; source?: string; live?: boolean; gateway?: boolean; name?: string;
}): React.ReactElement {
  const on = live === false ? "off" : "on";
  return (
    <tr className={"row-" + on}>
      <td className="ip">{ip}{gateway ? <span className="tag gw">网关</span> : null}</td>
      <td>{name ? <span className="devname" title={name}>{name}</span> : <span className="dim">—</span>}</td>
      <td className="mono">{mac || <span className="dim">—</span>}</td>
      <td>{iface ? <span className="iface">{iface}</span> : <span className="dim">—</span>}</td>
      <td><span className={"src src-" + (source || "arp")}>{source === "router" ? "路由器" : source === "sweep" ? "Ping" : "ARP"}</span></td>
      <td>{on === "on" ? <span className="dot on" /> : <span className="dot off" />}{on === "on" ? "在线" : "离线"}</td>
    </tr>
  );
}

/* 家庭总流量趋势:纯 div 柱状图(每根柱 = 一个 60s 刷新周期的全家庭流量增量)。
 * 数据来自 router_traffic_total 累计序列的前端差分;历史 <2 点不显示。 */
function TrafficChart({ rows, max }: { rows: { ts: number; delta: number }[]; max: number }): React.ReactElement {
  const spanMin = Math.round((rows[rows.length - 1].ts - rows[0].ts) / 60);
  const sumDelta = rows.reduce((a, r) => a + r.delta, 0);
  return (
    <div className="trend">
      <div className="trend-title">
        家庭流量趋势（近 {spanMin} 分钟，每 60s 采样 · 合计 {fmtBytes(sumDelta)}）
      </div>
      <div className="trend-bars">
        {rows.map((r, i) => (
          <div
            key={i}
            className={"trendbar" + (r.delta > 0 ? "" : " zero")}
            title={`${new Date(r.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false })} · ${fmtBytes(r.delta)}`}
            style={{ height: Math.max(2, Math.round((r.delta / max) * 100)) + "%" }}
          />
        ))}
      </div>
      <div className="trend-axis">
        <span>{new Date(rows[0].ts * 1000).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" })}</span>
        <span>柱 = 该周期全家庭流量（↑↓ 合计），高柱 = 有人在大量传数据</span>
        <span>{new Date(rows[rows.length - 1].ts * 1000).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit" })}</span>
      </div>
    </div>
  );
}

function Dashboard(): React.ReactElement {
  const [data, setData] = React.useState<DevicesPayload | null>(null);
  const [router, setRouter] = React.useState<RouterPayload | null>(null);
  const [err, setErr] = React.useState<string>("");
  const [kw, setKw] = React.useState("");
  const [onlyLive, setOnlyLive] = React.useState(false);
  const [lastFetch, setLastFetch] = React.useState<number>(0);

  const load = React.useCallback(async () => {
    setErr("");
    try {
      const [d, ro] = await Promise.all([apiDevices(), apiRouter().catch(() => null)]);
      setData(d);
      setRouter(ro);
      setLastFetch(Date.now());
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  /* IP → 路由器已知的设备名/厂商（HostName 优先，没有则用 DevBrands）。
   * 必须先于 shown 声明,shown 的过滤要按名字搜。 */
  const ipNames = React.useMemo(() => {
    const m = new Map<string, string>();
    if (router?.router_devices) {
      for (const d of router.router_devices) {
        if (!d.ip) continue;
        const label = d.hostname || d.vendor || "";
        if (label) m.set(d.ip, label);
      }
    }
    return m;
  }, [router]);

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
      if (!k) return true;
      const name = (ipNames.get(d.ip) || "").toLowerCase();
      return (
        d.ip.toLowerCase().includes(k) ||
        (d.mac || "").toLowerCase().includes(k) ||
        (d.iface || "").toLowerCase().includes(k) ||
        name.includes(k)
      );
    });
  }, [data, router, kw, onlyLive, ipNames]);

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

  /* 设备类型分布(路由器 IconType: mobile/laptop/computer/television/tablet/android)。 */
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
      .map(([k, n]) => ({ label: labelOf[k] || k, n }))
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
   * 取最近 180 点(60s 循环 ≈ 3 小时)。 */
  const trafficTrend = React.useMemo(() => {
    const s = router?.router_traffic_total;
    if (!s || s.length < 2) return null;
    const pts = s.slice(-180);
    let max = 1;
    const rows = pts.map((p, i) => {
      const delta = i === 0 ? 0 : Math.max(0, p.rx - pts[i - 1].rx + p.tx - pts[i - 1].tx);
      if (delta > max) max = delta;
      return { ts: p.ts, delta };
    });
    return { rows, max };
  }, [router]);
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
              {router.login_stale && (router.router_count ?? 0) > 0 ? (
                <div className="rtrstale">
                  ⏳ {router.login_stale_note || "本次登录失败/冷却中，以下沿用上次的终端数据"}（自动循环稍后重试，无需操作）
                </div>
              ) : null}
              {trafficTrend ? <TrafficChart rows={trafficTrend.rows} max={trafficTrend.max} /> : null}
              {(typeDist.length > 0 || freqDist.length > 0) ? (
                <div className="rtrdist">
                  {typeDist.length > 0 ? (
                    <div className="rtrdist-row">
                      <span className="rtrdist-label">设备类型</span>
                      {typeDist.map((t) => (
                        <span key={t.label} className="rtrdist-chip">{t.label} {t.n}</span>
                      ))}
                    </div>
                  ) : null}
                  {freqDist.length > 0 ? (
                    <div className="rtrdist-row">
                      <span className="rtrdist-label">Wi-Fi 频段</span>
                      {freqDist.map(([f, n]) => (
                        <span key={f} className={"rtrdist-chip" + (f.indexOf("5G") === 0 ? " g5" : " g24")}>{f} {n}</span>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
              {top5.length > 0 ? (
                <div className="rtrtop">
                  <div className="rtrtop-title">流量排行（累计下载 Top {top5.length}）</div>
                  {top5.map((item, i) => (
                    <div key={item.d.ip + item.d.mac} className="rtrtop-row">
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
              {router.router_devices && router.router_devices.length > 0 ? (
                <div className="rtrdevs">
                  {router.router_devices && router.router_devices.length > 5 ? (
                    <button className="rtrdevs-toggle" onClick={() => setShowAll(!showAll)}>
                      {showAll ? "收起完整列表" : `展开全部 ${router.router_count} 台终端`}
                    </button>
                  ) : null}
                  {(showAll ? router.router_devices : (router.router_devices?.slice(0, 5) ?? [])).map((d) => {
                    const st = !d.online ? "off" : isActive(d) ? "on" : "idle";
                    return (
                    <div key={d.ip + d.mac} className={"rtrdev " + (d.online ? "" : "off")}>
                      <span className="rtrdev-ip mono">{d.ip}</span>
                      <span className="rtrdev-host">{d.hostname || d.mac}</span>
                      {d.vendor ? <span className="rtrdev-vendor">{d.vendor}</span> : null}
                      {d.guest ? <span className="rtrdev-tag">访客</span> : null}
                      <span className="rtrdev-net">↑{fmtBytes(d.tx_bytes)} ↓{fmtBytes(d.rx_bytes)}</span>
                      {d.last_seen ? <span className="rtrdev-last">{d.last_seen.slice(5)}</span> : null}
                      <span className={"rtrdev-state st-" + st}>
                        {st === "off" ? "离线" : st === "idle" ? "挂起中" : "活跃"}
                      </span>
                      <span className={"rtrdev-dot " + st} />
                    </div>
                    );
                  })}
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

          {(data.stale || data.note) ? <div className={"banner " + (data.stale ? "warn" : "")}>{data.note}</div> : null}
          <div className="toolbar">
            <input
              value={kw}
              onChange={(e) => setKw(e.target.value)}
              placeholder="搜 IP / 设备名 / MAC / 网卡…"
            />
            <label className="chk"><input type="checkbox" checked={onlyLive} onChange={(e) => setOnlyLive(e.target.checked)} />只看在线</label>
            <span className="spacer" />
            <span className="hint">快照于 {fmtTs(data.scanned_at)} · 宿主 <code>make scan</code> 刷新数据源</span>
            <button className="btn" onClick={() => void load()}>刷新</button>
          </div>
          <div className="tablewrap">
            <table className="devs">
              <thead><tr><th>IP</th><th>设备名</th><th>MAC</th><th>网卡</th><th>来源</th><th>状态</th></tr></thead>
              <tbody>
                {shown.map((d) => (
                  <DeviceRow key={d.ip} ip={d.ip} mac={d.mac} iface={d.iface} source={d.source} live={d.live} gateway={d.gateway} name={ipNames.get(d.ip)} />
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
