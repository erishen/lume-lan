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
      <td><span className={"src src-" + (source || "arp")}>{source || "arp"}</span></td>
      <td>{on === "on" ? <span className="dot on" /> : <span className="dot off" />}{on === "on" ? "在线" : "离线"}</td>
    </tr>
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

  const shown = React.useMemo(() => {
    if (!data) return [];
    const k = kw.trim().toLowerCase();
    return data.devices.filter((d) => {
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
  }, [data, kw, onlyLive, ipNames]);

  /* 路由器终端流量 Top 5(按累计下载排序,数据已按该序下发)。 */
  const top5 = React.useMemo(() => {
    if (!router || !router.router_devices) return [];
    const list = [...router.router_devices].sort((a, b) => (b.rx_bytes ?? 0) - (a.rx_bytes ?? 0));
    const max = Math.max(1, list[0]?.rx_bytes ?? 1);
    return list.slice(0, 5).map((d) => ({ d, pct: Math.round(((d.rx_bytes ?? 0) / max) * 100) }));
  }, [router]);
  const [showAll, setShowAll] = React.useState(false);

  return (
    <section className="dash">
      {data && !data.missing ? (
        <>
          <div className="kpis">
            <div className="kpi"><div className="k">设备</div><div className="v">{data.count}</div><div className="u">台在册</div></div>
            <div className="kpi ok"><div className="k">在线</div><div className="v">{data.live_count}</div><div className="u">live=true</div></div>
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
                {(router.router_count ?? 0) > 0 ? <span className="routermeta">· {router.router_count} 台终端，在线 {router.router_online ?? "—"}</span> : null}
              </div>
              {router.login_stale && (router.router_count ?? 0) > 0 ? (
                <div className="rtrstale">
                  ⏳ {router.login_stale_note || "本次登录失败/冷却中，以下沿用上次的终端数据"}（自动循环稍后重试，无需操作）
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
                      {item.d.online ? <span className="rtrtop-live">在线</span> : null}
                      <div className="rtrtop-bar"><div style={{ width: item.pct + "%" }} /></div>
                      <span className="rtrtop-net">↓{fmtBytes(item.d.rx_bytes)} · ↑{fmtBytes(item.d.tx_bytes)}</span>
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
                  {(showAll ? router.router_devices : (router.router_devices?.slice(0, 5) ?? [])).map((d) => (
                    <div key={d.ip + d.mac} className={"rtrdev" + (d.online ? "" : " off")}>
                      <span className="rtrdev-ip mono">{d.ip}</span>
                      <span className="rtrdev-host">{d.hostname || d.mac}</span>
                      {d.vendor ? <span className="rtrdev-vendor">{d.vendor}</span> : null}
                      {d.guest ? <span className="rtrdev-tag">访客</span> : null}
                      <span className="rtrdev-net">↑{fmtBytes(d.tx_bytes)} ↓{fmtBytes(d.rx_bytes)}</span>
                      {d.last_seen ? <span className="rtrdev-last">{d.last_seen.slice(5)}</span> : null}
                      <span className={"rtrdev-dot" + (d.online ? " on" : "")} />
                    </div>
                  ))}
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
