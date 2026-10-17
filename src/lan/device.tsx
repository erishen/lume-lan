/* 设备相关组件: 统一设备行 / WAN 状态卡 / 单设备详情卡。 */
import React from "react";
import type { RouterDevice, RouterPayload } from "../api";
import { fmtBytes } from "../api";
import { Rssi } from "./charts";
import type { LanRow } from "./types";

/* 统一设备行(设备表用): 状态点 + IP/网关 + 设备名/厂商/访客 + MAC + 流量。
 * state 3 态(与终端列表一致): on=在线·活跃 idle=在线·挂起 off=离线。
 * 行点击 = 选中看详情/单设备曲线。 */
export function DeviceRow({ d, selIp, onSelect }: { d: LanRow; selIp: string; onSelect: (ip: string) => void }): React.ReactElement {
  const st = d.state;
  return (
    <tr className={"row-" + st + (selIp === d.ip ? " sel" : " click")} onClick={() => onSelect(d.ip)}>
      <td className="st">
        <span className={"dot " + st} />
        {st === "off" ? "离线" : st === "idle" ? "挂起中" : "活跃"}
      </td>
      <td className="ip">{d.ip}{d.gateway ? <span className="tag gw">网关</span> : null}</td>
      <td>
        {d.name ? <span className="devname" title={d.name}>{d.name}</span> : <span className="dim">—</span>}
        {d.vendor ? <span className="rtrdev-vendor">{d.vendor}</span> : null}
        {d.guest ? <span className="rtrdev-tag">访客</span> : null}
      </td>
      <td className="mono">{d.mac || <span className="dim">—</span>}</td>
      <td className="net">
        {d.rx !== undefined || d.tx !== undefined
          ? <>↓{fmtBytes(d.rx)} · ↑{fmtBytes(d.tx)}</>
          : <span className="dim">—</span>}
      </td>
    </tr>
  );
}

/* WAN 状态卡: 默认/主连接的状态(连接态/接入方式/IPv4/IPv6/DNS/NAT 类型/带宽峰值)。 */
export function WanCard({ wan }: { wan: RouterPayload["router_wan"] }): React.ReactElement | null {
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

/* 单设备详情卡: 点选设备时展示它的全部字段(MAC/厂商/类型/频段/信号/接入方式/
 * 在线态/最后活跃/累计流量/近 1h 增量/平均速率)。曲线没有历史时这里兜底,
 * 保证「点一下总能看到这台设备的具体情况」。 */
export function DeviceDetail({ d, name, row }: { d: RouterDevice; name: string; row?: LanRow }): React.ReactElement {
  const typeLabel: Record<string, string> = { mobile: "手机", laptop: "笔记本", computer: "电脑", television: "电视", tablet: "平板", android: "Android" };
  const cells: [string, React.ReactNode][] = [
    ["设备名", <>{name || d.mac}{d.guest ? <span className="rtrdev-tag">访客</span> : null}</>],
    ["MAC", <span className="mono">{d.mac || "—"}</span>],
    ["网卡", row?.iface || "—"],
    ["厂商", d.vendor || "—"],
    ["类型", d.icon ? (typeLabel[d.icon] || d.icon) : "—"],
    ["频段", d.freq || "—"],
    ["信号", d.access_type === "Wireless" ? <Rssi rssi={d.rssi} linkQuality={d.link_quality} /> : d.access_type === "Ethernet" ? "有线" : "—"],
    ["来源", row ? (row.source === "router" ? "路由器" : row.source === "sweep" ? "Ping" : "ARP") : "—"],
    ["最后活跃", d.last_seen ? <span className="mono">{d.last_seen.slice(5, 16)}</span> : "—"],
    ["累计 下载/上传", <span className="mono">↓{fmtBytes(d.rx_bytes)} · ↑{fmtBytes(d.tx_bytes)}</span>],
    ["近 1h 增量", d.delta_1h !== undefined && d.delta_1h !== null ? <span className="mono">{fmtBytes(d.delta_1h)}（≈{fmtBytes(Math.round(d.delta_1h / 3600))}/s）</span> : "—"],
    ["状态", row
      ? <span className={row.state === "on" ? "det-on" : row.state === "idle" ? "det-idle" : "det-off"}>{row.state === "off" ? "离线" : row.state === "idle" ? "挂起中" : "活跃"}</span>
      : d.online ? <span className="det-on">{d.delta_1h !== null && d.delta_1h !== undefined && d.delta_1h >= 1048576 ? "活跃" : "挂起中"}</span> : <span className="det-off">离线</span>],
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
