/* Shared API layer + types for the Lume LAN dashboard. All endpoints live
 * on the Lume server (views root www/, so plain root URLs). */

export interface Device {
  ip: string;
  mac: string;
  iface?: string;
  live?: boolean;
  source?: string;
  gateway?: boolean;
}

export interface DevicesPayload {
  ok: boolean;
  missing: boolean;
  gateway: string;
  subnet: string;
  scanned_at: number;
  sweep: boolean;
  count: number;
  live_count: number;
  devices: Device[];
  stale: boolean;
  note: string;
}

export interface Meta {
  gateway: string;
  subnet: string;
  sweep: boolean;
  scanned_at: number;
  available: boolean;
}

export interface RouterDevice {
  ip: string;
  mac: string;
  hostname?: string;
  vendor?: string;
  rx_bytes?: number;
  tx_bytes?: number;
  up_rate?: number;
  down_rate?: number;
  rssi?: number | null;
  online?: boolean;
  wired?: boolean | null;
  guest?: boolean;
  last_seen?: string;
  /** 近 1 小时流量增量(字节); null/undefined = 历史不足未知。 */
  delta_1h?: number | null;
  /** 路由器识别的设备类型(mobile/laptop/computer/television/tablet/android)。 */
  icon?: string;
  /** Wi-Fi 频段(5GHz/2.4GHz)。 */
  freq?: string;
}

/** 家庭总流量序列的一个采样点(宿主每 60s 记一次, 全设备 rx+tx 累计求和)。 */
export interface TrafficPoint {
  ts: number;
  rx: number;
  tx: number;
  /** 本周期流量增量最大的设备 IP(主因, 可能是空串)。 */
  top_ip?: string;
  /** 主因设备名(备注/厂商), 供峰值柱标注。 */
  top_name?: string;
  /** 主因设备本周期增量(字节)。 */
  top_delta?: number;
}

export interface RouterPayload {
  available: boolean;
  note?: string;
  host?: string;
  model?: string;
  friendly?: string;
  serial?: string;
  manufacturer_oui?: string;
  router_ok?: boolean;
  terminal_source?: string;
  login_ok?: boolean;
  login_error?: string;
  login_stale?: boolean;
  login_stale_note?: string;
  fetched_at?: number;
  router_devices?: RouterDevice[];
  router_count?: number;
  router_online?: number;
  /** 在线且近 1h 有实际流量(≥1MB)的设备数, 区别于「挂起中」的心跳设备。 */
  router_active?: number;
  /** 家庭总流量序列(全设备 rx+tx 累计求和, 每 60s 一个点), 用于趋势曲线。 */
  router_traffic_total?: TrafficPoint[];
  /** 本地命名层(.data/aliases.json): {ip: 名字}。显示时优先级高于路由器备注。 */
  router_aliases?: Record<string, string>;
}

export async function apiGet<T>(url: string): Promise<T> {
  const r = await window.fetch(url);
  if (!r.ok) throw new Error(url + " -> HTTP " + r.status);
  return (await r.json()) as T;
}

export function apiDevices(): Promise<DevicesPayload> {
  return apiGet<DevicesPayload>("/api/devices");
}

export function apiMeta(): Promise<Meta> {
  return apiGet<Meta>("/api/meta");
}

export function apiRouter(): Promise<RouterPayload> {
  return apiGet<RouterPayload>("/api/router");
}

export function fmtBytes(n?: number): string {
  if (n === undefined || n === null || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return v.toFixed(v >= 10 || i === 0 ? 0 : 1) + " " + units[i];
}

export function fmtTs(unix: number): string {
  if (!unix) return "—";
  const d = new Date(unix * 1000);
  return d.toLocaleString("zh-CN", { hour12: false });
}

export function fmtAge(unix: number): string {
  if (!unix) return "—";
  const sec = Math.max(0, Math.floor(Date.now() / 1000) - unix);
  if (sec < 60) return sec + " 秒前";
  if (sec < 3600) return Math.floor(sec / 60) + " 分钟前";
  return Math.floor(sec / 3600) + " 小时前";
}
