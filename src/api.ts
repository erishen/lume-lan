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
