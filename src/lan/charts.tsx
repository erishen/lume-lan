/* 图表组件: 家庭总流量趋势 / 单设备流量趋势 / 信号强度柱。 */
import React from "react";
import { fmtBytes } from "../api";

/* 家庭总流量趋势:纯 div 柱状图(每根柱 = 一个 60s 刷新周期的全家庭流量增量)。
 * 数据来自 router_traffic_total 累计序列的前端差分;历史 <2 点不显示。
 * 峰值柱高亮,并标注「峰值时刻 + 主因设备」(谁在吃带宽)。 */
export function TrafficChart({ rows, max, peakIdx, peak }: {
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

/* 单设备流量趋势:纯 div 柱状图(每根柱 = 该设备一个 60s 周期的流量增量,↑↓ 合计)。
 * 数据来自 /api/router-traffic 的该设备累计序列, 前端差分; 历史 <2 点不显示。
 * 头部标注设备名/IP + 窗口内 下载/上传/合计, 点「×」收起。 */
export function PerDeviceChart({ device, rows, max, win }: {
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

/* 信号强度柱(3 段, 按 rssi 0~100 映射 good/mid/bad; 有线/未知显示 —)。
 * rssi 来自 device/topology(HostInfo 的 RSSI 恒 0)。 */
export function Rssi({ rssi, wired, linkQuality }: { rssi?: number | null; wired?: boolean | null; linkQuality?: string }): React.ReactElement {
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
