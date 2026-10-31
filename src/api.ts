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
  /** 统一数据源状态: 快照是否读到(缺文件=false)。 */
  reachable?: boolean;
  /** 快照生成时刻(unix)。 */
  fetched_at?: number;
  /** 失败原因(正常为空串)。 */
  error?: string;
}

export interface Meta {
  gateway: string;
  host_ip?: string;
  subnet: string;
  sweep: boolean;
  scanned_at: number;
  available: boolean;
  reachable?: boolean;
  fetched_at?: number;
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
  /** 近 5h 增量(每 IP 300 点序列首尾 rx/tx 差; 历史不足 2 点的设备不在表内)。
   * 宿主 router_fetch.py 生成, 供「近 5h」流量排行。 */
  hist5h?: Record<string, { rx: number; tx: number; n: number }>;
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
  /** 失败原因(正常为空串)。 */
  error?: string;
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

/** 可互访的 lume peer 节点(.data/peers.json, 宿主 peers_fetch.py 探测产出)。 */
export interface PeerTool { name: string; desc?: string; server?: string }
export interface PeerNode {
  /** host:port */
  target: string;
  enterable?: boolean;
  reachable: boolean;
  /** 对端 PEER_NAME */
  name: string;
  /** 对端 PEER_SITE_NAME(博客这类应用会给) */
  site: string;
  tools: PeerTool[];
  /**
   * 是否有可问答的 agent(探测 /peer/agent 得出)。
   * 纯数据节点(博客)没有这条路由 —— 界面据此决定是否给提问框。
   */
  agent?: boolean;
  /** /peer/info 的只读概要(可选端点, 只有声明了它的节点才有) */
  info?: Record<string, unknown>;
  error?: string;
  fetched_at?: number;
  latency_ms?: number;
}
/** 审批链上的一关(来自任务的 metadata.payload._chain) */
export interface ApprovalStage {
  stage: string;   // first | second | final
  role: string;
  node: string;
}

/** 一条待办审批(非终结态的 A2A 任务) */
export interface PendingTask {
  id: string;
  state: string;          // submitted | working | input-required
  tool: string;
  target: string;
  domain: string;
  policy: ApprovalStage[];
  approved: string[];     // 已通过的角色
  waiting_for: string;    // 当前该谁审(执行方给的人话)
  since: number;
  source: string;
  /** 这一关是否轮到本节点 —— 服务端算好下发, 前端只读(别在这儿重算规则) */
  can_act?: boolean;
  /** 下一关该由哪个节点审(空串 = 人工终审) */
  next_node?: string;
  /** can_act 为 false 的原因, 直接显示给人的话 */
  blocked_reason?: string;
}

/** 一条审计记录(a2a-audit.jsonl 尾部) */
export interface AuditRow {
  at: number;
  /**
   * 事件名。分两类, 回答不同问题:
   *   approval_required / approval_step / approval_denied / approval_granted
   *       → 谁批准了什么(决策链, 合规追溯用)
   *   a2a_call / task_finished
   *       → 发起了什么、对端怎么答、多快(链路健康, 排障与 SLA 用)
   */
  event: string;
  task_id: string;
  role: string;
  required_role: string;
  claimed_role: string;
  /** a2a_call 专用: 目标节点 / 协议方法 / 结果分类 / HTTP 状态 / 耗时 */
  target?: string;
  method?: string;
  outcome?: string;         // ok | denied | unreachable | bad_response
  status?: number;
  elapsed_ms?: number;
  detail?: string;
  /** 审批入口: lan-ui(看板点) | cli(脚本发) | unknown */
  origin?: string;
  actor?: string;
  tool?: string;
  decision: string;
  reason: string;
  /**
   * 这条记录来自哪一侧 —— 合并了两份审计, 这个字段是区分依据:
   *   "lan-ui"        → 看板自己发出的那一跳(可能没到对端)
   *   "<执行方名>"     → 执行方收到并处理的调用(如 "lume-blog")
   * 同一次调用在两边都有时会出现两条: 这正说明"发出去了, 对方也处理了"。
   * 只有一侧有, 则能直接判断失败发生在哪一段。
   */
  source: string;
}

export interface PeersPayload {
  available: boolean;
  /** 是否配了 PEER_SHARED; false 时整个 peer 面未启用 */
  enabled: boolean;
  reachable: boolean;
  stale?: boolean;
  age_secs?: number;
  fetched_at?: number;
  targets?: string[];
  up_count?: number;
  total_count?: number;
  peers?: PeerNode[];
  /** 待办审批(非终结态任务), 按时间倒序 */
  pending?: PendingTask[];
  /** 审计尾部 */
  audit?: AuditRow[];
  /** 采集读取的 a2a 状态目录(未配置时为空) */
  state_dirs?: string[];
  error?: string;
  note?: string;
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
  /** 统一数据源状态: deviceinfo 免密身份层是否可达。 */
  reachable?: boolean;
  /** 本轮是否沿用上次终端数据(登录失败/冷却)。 */
  stale?: boolean;
  /** 登录/身份层失败原因(正常为空串)。 */
  error?: string;
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

/** 调对端工具的结果。ok=false 时 error 是给人看的一句话。 */
export interface PeerInvokeResult {
  ok: boolean;
  tool?: string;
  target?: string;
  elapsed_ms?: number;
  result?: unknown;
  error?: string;
  agent_status?: number;
}

/**
 * 在看板上直接调用对端的一个工具。
 *
 * `peer` 传 target 或节点名都行(服务端两种都认); 工具名会被服务端的
 * **能力清单**校验 —— 不在对端声明里的名字会被拒, 这里不做前端预判,
 * 免得两边规则漂移。
 */
export async function apiPeerInvoke(
  peer: string, tool: string, args: Record<string, unknown> = {},
): Promise<PeerInvokeResult> {
  const r = await window.fetch("/api/peer/invoke", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ peer, tool, arguments: args }),
  });
  if (!r.ok) return { ok: false, error: "/api/peer/invoke -> HTTP " + r.status };
  return (await r.json()) as PeerInvokeResult;
}

export interface PeerAskResult {
  ok: boolean;
  target?: string;
  elapsed_ms?: number;
  answer?: string;
  notes?: string[];
  error?: string;
}

/** 向对端 agent 提一个问题(同步等回答, 不是 A2A 的异步任务)。 */
export async function apiPeerAsk(peer: string, question: string): Promise<PeerAskResult> {
  const r = await window.fetch("/api/peer/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ peer, question }),
  });
  if (!r.ok) return { ok: false, error: "/api/peer/ask -> HTTP " + r.status };
  return (await r.json()) as PeerAskResult;
}

/** 与 agent 对话的结果。*/
export interface ChatResult {
  ok: boolean;
  answer?: string;
  /** agent 每轮调用工具的痕迹, 如 "peers__crm__crm.stats" —— 让用户知道数据哪来的 */
  notes?: string[];
  elapsed_ms?: number;
  complete?: boolean;
  error?: string;
}

/**
 * 和本机 agent 说一句话。
 *
 * 同步等答完(不是流式): 后端 lume 的 http_post 收完整响应才返回, 没法边收边转发。
 * 所以前端要显示"等待中", 并用返回的 notes 展示它查了哪些节点。
 */
export async function apiChat(message: string, sessionId?: string): Promise<ChatResult> {
  const r = await window.fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, sessionId: sessionId || "" }),
  });
  if (!r.ok) return { ok: false, error: "/api/chat -> HTTP " + r.status };
  return (await r.json()) as ChatResult;
}

export function apiPeers(): Promise<PeersPayload> {
  return apiGet<PeersPayload>("/api/peers");
}

/** 审批结果。ok=false 时 error 是给人看的完整句子(服务端已含"该由谁审")。 */
export interface ApproveResult {
  ok: boolean;
  error?: string;
  id?: string;
  role?: string;
  decision?: string;
  state?: string;
  required_role?: string;
  required_node?: string;
}

/**
 * 批准/驳回一条待办。
 *
 * 只传 id 与 decision: **角色由服务端自己算**(它知道这一关轮到谁), 前端传了
 * 也不采信。这样"看板能不能批"和"谁有资格批"是同一个判定, 不会因为前端逻辑
 * 放松而变成任意人可代批。
 */
export async function apiApprove(id: string, decision: "approve" | "reject",
                                 note?: string): Promise<ApproveResult> {
  const r = await window.fetch("/api/approve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, decision, note: note || "" }),
  });
  // 服务端对"不该你批"返回 200 + ok:false(业务拒绝, 不是协议错误),
  // 所以这里不能只看 HTTP 状态码。
  if (!r.ok) {
    let msg = "/api/approve -> HTTP " + r.status;
    try {
      const j = (await r.json()) as ApproveResult;
      if (j && j.error) msg = j.error;
    } catch {
      /* 非 JSON 错误页, 保留状态码文案 */
    }
    return { ok: false, error: msg };
  }
  return (await r.json()) as ApproveResult;
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
