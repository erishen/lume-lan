#!/usr/bin/env python3
"""daily_digest.py — 局域网日报: 每 24h 推一条到 webhook(钉钉/企微), 也可手动强推。

内容(全用现成数据, 无需额外抓取):
  📶 在线/活跃台数   ← .data/router.json(router_devices)
  📊 自上次日报以来的增量流量 Top5   ← 设备累计 rx/tx 差分(路由器计数器单调累计)
  🌐 WAN 状态 + 峰值
  ⚠️ 上次日报以来的告警事件数(异常流量) ← .data/alert_state.json 的 anomaly_ts

节奏:
  - make dev / 容器数据循环里, router_fetch.py 每轮调 maybe_send(): 距上次 >=24h 才推
  - make daily-digest: 不等 24h, 立即推一条(测试/手动)
未配 WEBHOOK_URL 时只打印到日志, 不影响数据循环。
"""
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, ".data", "digest_state.json")
ROUTER = os.path.join(ROOT, ".data", "router.json")
ALERTS = os.path.join(ROOT, ".data", "alert_state.json")
DAILY_SECS = 24 * 3600


def _load_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(st: dict) -> None:
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE)


def _fmt_gb(n: float) -> str:
    if n >= 1024 ** 4:
        return f"{n / 1024 ** 4:.2f} TB"
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.0f} MB"
    return f"{n / 1024:.0f} KB"


def build_digest(devices: list, wan: dict, now: int) -> str:
    """生成日报正文(纯数据, 不推送)。"""
    aliases = _load_json(os.path.join(ROOT, ".data", "aliases.json"))
    last_state = _load_json(STATE)
    last_totals = last_state.get("totals") or {}

    def label(d):
        ip = str(d.get("ip") or "?")
        return aliases.get(ip) or d.get("hostname") or d.get("vendor") or ip

    online = [d for d in devices if d.get("online")]
    active = [d for d in online
              if d.get("delta_1h") is None or d.get("delta_1h", 0) >= 1048576]

    # 自上次日报以来的增量: 累计计数器差分(计数器不重置时 = 区间真实流量)
    inc = []
    for d in devices:
        ip = str(d.get("ip") or "?")
        cur = int(d.get("rx_bytes") or 0) + int(d.get("tx_bytes") or 0)
        prev = int(last_totals.get(ip, 0) or 0)
        inc.append((label(d), ip, max(0, cur - prev), cur))
    inc.sort(key=lambda x: x[2], reverse=True)

    # 上次日报以来的异常流量事件数
    anomaly_events = 0
    if last_state.get("last_ts"):
        astate = _load_json(ALERTS)
        for ts in (astate.get("anomaly_ts") or {}).values():
            if int(ts or 0) > int(last_state["last_ts"]):
                anomaly_events += 1

    L = [f"lume-lan 日报({time.strftime('%m-%d %H:%M', time.localtime(now))}):"]
    L.append(f"📶 在线 {len(online)} 台 / 在册 {len(devices)} 台, 其中活跃 {len(active)} 台")
    L.append("📊 自上次日报以来流量 Top5: "
             + ("、".join(f"{n} {(_fmt_gb(v))}" for n, _, v, _ in inc[:5] if v > 0)
                if any(x[2] > 0 for x in inc[:5]) else "无(计数器未变)"))
    wan_line = ""
    if wan.get("status"):
        peak_d = wan.get("down_max_kbps") or 0
        wan_line = f"({wan.get('status')}" + (f" · 峰值↓{peak_d / 1000:.1f}Mbps" if peak_d else "") + ")"
    L.append(f"🌐 WAN {wan_line or '未知'}" + (f" · 关键设备告警 {anomaly_events} 起" if anomaly_events else ""))
    return "\n".join(L)


def maybe_send(devices: list, wan: dict, now: int, force: bool = False):
    """到 24h 推一条(force 则立即)。返回 (是否发送, 说明)。"""
    if not devices:
        return False, "无终端数据, 跳过"
    last = _load_json(STATE)
    if not force and now - int(last.get("last_ts") or 0) < DAILY_SECS:
        mins = (now - int(last.get("last_ts") or 0)) // 60
        return False, f"距上次日报 {mins} 分钟, 未到 24h"
    text = build_digest(devices, wan, now)
    # 记状态: 当前累计值做下次差分基线 + 发送时间
    last["last_ts"] = now
    last["totals"] = {str(d.get("ip")): int(d.get("rx_bytes") or 0) + int(d.get("tx_bytes") or 0)
                      for d in devices if d.get("ip")}
    _save_state(last)
    try:
        import webhook_notify
    except ImportError:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import webhook_notify
    ok, detail = webhook_notify.send_text(text)
    print(text.replace("\n", "\n  "))
    print(f"  日报: {detail}")
    return ok, detail


if __name__ == "__main__":
    import sys
    r = _load_json(ROUTER)
    devs = r.get("router_devices") or []
    if not devs:
        print("✗ 无终端数据, 先跑 make router-fetch")
        sys.exit(1)
    ok, detail = maybe_send(devs, r.get("router_wan") or {}, int(time.time()), force=True)
    sys.exit(0 if ok or "未配置" in detail else 1)
