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
  fetched_at?: number;
  router_devices?: RouterDevice[];
  router_count?: number;
  router_online?: number;
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
