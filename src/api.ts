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
  /** 本机在局域网里的 IP(配 AdGuard DNS「首选」要指向它)。 */
  host_ip?: string;
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
  host_ip?: string;
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
  /** 信号强度(0~100), 来自 device/topology(HostInfo 的 RSSI 恒为 0)。 */
  rssi?: number | null;
  /** 链路质量(High/Medium/Low)。 */
  link_quality?: string;
  /** 接入方式(Wireless/Ethernet)。 */
  access_type?: string;
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

/** WAN 状态(来自 ntwk/wan, 默认/主连接)。 */
export interface WanStatus {
  status?: string;
  connection?: string;
  access_type?: string;
  ipv4_addr?: string;
  ipv4_mask?: string;
  ipv4_gateway?: string;
  ipv4_addr_type?: string;
  dns?: string;
  ipv6_addr?: string;
  ipv6_status?: string;
  nat_type?: number;
  mtu?: number;
  /** 带宽峰值(kbps): 路由器自带的 *BandwidthMax(历史累计最大值, 权威)。 */
  up_max_kbps?: number;
  down_max_kbps?: number;
  /** 当前速率(kbps): *BandwidthHistory 最后一个采样点(最近采样周期实测值)。 */
  up_now_kbps?: number;
  down_now_kbps?: number;
  conn_count?: number;
}

/** 拓扑节点信号(device/topology 按 mac 索引)。 */
export interface TopoNode {
  rssi?: number;
  link_quality?: string;
  access_type?: string;
  active?: boolean;
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

/** 单设备流量历史的一个采样点(累计值, 宿主每 60s 记一次, 保留 300 点 ≈ 5h)。 */
export interface DeviceTrafficPoint {
  ts: number;
  rx: number;
  tx: number;
}

/** 每设备流量历史响应:{ip: 累计值序列}。 */
export interface RouterTrafficPayload {
  available: boolean;
  note?: string;
  /** {ip: DeviceTrafficPoint[]} 每设备的累计流量序列(按 ts 升序)。 */
  series?: Record<string, DeviceTrafficPoint[]>;
  ip_count?: number;
}

/* DNS 域名访问记录(AdGuard Home 查询日志聚合, 宿主 make dns-fetch 轮询)。
 * reachable=false → AdGuard 没连上, 前端降级显示; clients 按客户端 IP 聚合。 */
export interface DnsDomain {
  domain: string;
  count: number;
  blocked: number;
  last_ts?: number;
}
export interface DnsClient {
  name?: string;
  total: number;
  blocked: number;
  last_ts?: number;
  top?: DnsDomain[];
}
export interface DnsPayload {
  available: boolean;
  /** AdGuard 可达(查询日志实时拉取成功); false 时 clients 可能是降级旧数据。 */
  reachable?: boolean;
  stale?: boolean;
  note?: string;
  fetched_at?: number;
  window_secs?: number;
  query_count?: number;
  client_count?: number;
  /** {clientIp: DnsClient} 近 1h 每客户端 DNS 查询聚合。 */
  clients?: Record<string, DnsClient>;
  /** 全家域名排行 Top10(近 1h)。 */
  top_domains?: DnsDomain[];
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
  /** WAN 状态卡(ntwk/wan: 连接态/接入方式/IPv4/IPv6/DNS/NAT/带宽峰值)。 */
  router_wan?: WanStatus;
  /** 信号强度拓扑(device/topology: mac→{rssi,link_quality,access_type})。 */
  router_topology?: Record<string, TopoNode>;
  /** 异常流量告警阈值(GB/近 1h, env LAN_ANOMALY_GB 可配, 默认 10)。 */
  anomaly_gb?: number;
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

export function apiRouterTraffic(): Promise<RouterTrafficPayload> {
  return apiGet<RouterTrafficPayload>("/api/router-traffic");
}

export function apiDns(): Promise<DnsPayload> {
  return apiGet<DnsPayload>("/api/dns");
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
