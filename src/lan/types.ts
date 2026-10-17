/* 共享类型与工具（设备行 / 活跃判定）。 */
import type { RouterDevice } from "../api";

/* 近 1h 流量增量阈值: ≥ 1MB 视为「活跃」, 否则「挂起中」(心跳级)。
 * delta_1h 为 null(历史刚建、未知)时, 在线设备默认按活跃显示, 不误伤。 */
export function isActive(d: RouterDevice): boolean {
  if (!d.online) return false;
  const delta = d.delta_1h;
  return delta === undefined || delta === null ? true : delta >= 1048576;
}

/* 统一设备行(设备表用): ARP 快照(本机 make scan)+ 路由器全量终端(在线+离线)合并,
 * 附路由器侧富字段(厂商/类型/信号/流量/频段)。state 3 态(与终端列表一致):
 * on=在线·活跃 idle=在线·挂起 off=离线。行点击 = 选中看详情/单设备曲线。 */
export interface LanRow {
  ip: string; mac: string; iface?: string; source?: string; live?: boolean;
  gateway?: boolean; name?: string; vendor?: string; icon?: string; freq?: string;
  rssi?: number | null; link_quality?: string; wired?: boolean | null; guest?: boolean;
  rx?: number; tx?: number; last_seen?: string; state: "on" | "idle" | "off";
}
