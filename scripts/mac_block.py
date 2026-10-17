#!/usr/bin/env python3
"""mac_block.py — 路由器 MAC 黑名单管理(拉黑 / 解除 / 名单)。

数据面: 华为 WS5102 的 ntwk/wlanfilterenhance(每 SSID 一条, 含 MAC 准入开关 + 黑名单)。
本地留底: .data/blocklist.json —— 路由器重启会丢配置, 这份是"我们拉过谁"的账本,
也是 UI 拉黑标记 / 恢复出厂后的重建依据。

用法(make 目标见 Makefile):
  make block 192.168.3.99            # 按 IP 拉黑(自动解析成 MAC; 记 reason)
  make block 84:9D:C2:72:7A:69       # 按 MAC 直接拉黑
  make unblock 192.168.3.99          # 解除(从路由器黑名单 + 本地账本移除)
  make block-list                     # 看当前名单(路由器实态 + 本地账本)

安全闸(默认拒绝, --force 覆盖):
  - 网关 MAC / CRITICAL_IPS / 有本地备注或路由器备注的设备 → 大概率是自家设备, 拉黑=断网
  - 每次 add 必须记 reason(默认 "未知设备手动拉黑")
  拉黑/解除后回读路由器验证(不信任"POST 200 就是成功了"——CAT 写操作可能静默失败)。

限流: 每个命令 = 1 次登录, 别连发(Too_Many_user 冷却 5min)。dry-run: 加 --dry-run
只打印将要写入的 body, 不发请求(限流时也能预演)。
"""
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import router_fetch as rf
import urllib.parse

BLOCKLIST = os.path.join(ROOT, ".data", "blocklist.json")
FILTER_API = "ntwk/wlanfilterenhance"


def _load_blocklist() -> dict:
    try:
        with open(BLOCKLIST, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_blocklist(st: dict) -> None:
    tmp = BLOCKLIST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, BLOCKLIST)


def _normalize_mac(s: str) -> str:
    m = re.sub(r"[:-]", "", s.upper())
    return m if len(m) == 12 else ""


def _resolve_mac(sess, ip: str, devices: list) -> str:
    """IP → MAC(先查本地快照, 再问路由器 getmacaddress)。"""
    for d in devices:
        if str(d.get("ip")) == ip and d.get("mac"):
            return str(d["mac"])
    c, r = sess.get_json(f"/api/ntwk/getmacaddress?ipaddress={urllib.parse.quote(ip)}")
    if isinstance(r, dict) and r.get("macaddress"):
        return str(r["macaddress"])
    return ""


def _blocked_mac_set(sess) -> set:
    """当前路由器黑名单(全部 SSID 的 MAC 列表并集, 小写无分隔)。"""
    c, d = sess.get_json(f"/api/{FILTER_API}")
    out = set()
    for item in d if isinstance(d, list) else []:
        if not isinstance(item, dict):
            continue
        for k in ("BMACAddresses", "MACAddressBlackList", "BlackMACAddresses"):
            for m in item.get(k) or []:
                n = _normalize_mac(str(m))
                if n:
                    out.add(n)
    return out


def _post_filter(sess, items: list) -> bool:
    """POST 整份(改过的)黑名单配置。返回 HTTP 是否 200(真正的验证靠回读)。"""
    body = json.dumps(items, ensure_ascii=False).encode()
    code, _raw = sess._req("POST", f"/api/{FILTER_API}", body)
    return code == 200


def _gateway_mac() -> str:
    try:
        d = json.load(open(os.path.join(ROOT, ".data", "devices.json")))
        for dev in d.get("devices", []):
            if dev.get("gateway"):
                return _normalize_mac(str(dev.get("mac") or ""))
    except (OSError, ValueError):
        pass
    return ""


def cmd(action: str, target: str, reason: str, force: bool, dry: bool):
    env = rf.load_env()
    host = env.get("ROUTER_HOST", "192.168.3.1")
    if not host.startswith("http"):
        host = "http://" + host
    sess = rf.CatSession(host)
    lg = sess.login(env.get("ROUTER_USER", "admin"), env.get("ROUTER_PASS", ""))
    if not lg.get("ok"):
        print(f"✗ 登录失败({lg.get('error', '')[:80]}), 限流冷却 5min 后重试")
        sys.exit(1)

    r = json.load(open(rf.OUT, encoding="utf-8"))
    devices = r.get("router_devices", [])

    # list: 路由器实态 + 本地账本
    if action == "list":
        router_bl = _blocked_mac_set(sess)
        local_bl = _load_blocklist()
        print(f"路由器黑名单({len(router_bl)}): " + ("、".join(sorted(router_bl)) or "(空)"))
        print(f"本地账本({len(local_bl)}):")
        for mac, info in local_bl.items():
            print(f"  🚫 {mac}  {info.get('ip', '?')}  {info.get('label', '')}  "
                  f"reason={info.get('reason', '')}  at={info.get('blocked_at', '')}")
        return

    mac = _normalize_mac(target) if re.fullmatch(r"[0-9A-Fa-f:]{10,17}", target.replace("-", ":")) \
        else _resolve_mac(sess, target, devices)
    if not mac:
        print(f"✗ 解析不出 MAC: {target}(IP 未在册且路由器查不到)")
        sys.exit(1)
    mac = mac.lower()

    # 安全闸
    bl_state = _load_blocklist()
    label = next((d.get("hostname") or d.get("vendor") or ""
                   for d in devices if _normalize_mac(str(d.get("mac", ""))) == mac), "")
    ip = next((str(d.get("ip")) for d in devices
               if _normalize_mac(str(d.get("mac", ""))) == mac), "")
    critical = {x.strip() for x in (env.get("CRITICAL_IPS") or "").split(",") if x.strip()}
    aliases = {}
    try:
        aliases = json.load(open(os.path.join(ROOT, ".data", "aliases.json")))
    except (OSError, ValueError):
        pass
    reasons = []
    if _normalize_mac(_gateway_mac()) == mac:
        reasons.append("网关")
    if ip in critical:
        reasons.append(f"CRITICAL_IPS({ip})")
    if aliases.get(ip) or label:
        reasons.append(f"已知设备({label or aliases.get(ip, ip)})")
    if reasons and action == "add" and not force:
        print(f"✗ 拒绝拉黑 {ip or target}({mac}): {', '.join(reasons)}")
        print(f"  确认要拉(=断网该设备)请重跑并加 --force")
        sys.exit(2)
    if action == "add" and mac in bl_state:
        print(f"= {mac} 已在本地账本, 跳过(路由器侧仍会重新写入)")

    # 取当前完整配置(全部 SSID 条目), 改黑名单
    c, items = sess.get_json(f"/api/{FILTER_API}")
    if not isinstance(items, list) or not items:
        print("✗ wlanfilterenhance 取不到配置, 无法修改")
        sys.exit(1)
    key_added = key_removed = False
    for item in items:
        for k in ("BMACAddresses", "MACAddressBlackList", "BlackMACAddresses"):
            if k not in item:
                continue
            lst = [m for m in (item.get(k) or []) if _normalize_mac(str(m)) != mac]
            if action == "remove":
                item[k] = lst
                key_removed = True
            else:
                item[k] = lst + [mac.replace("-", "")]
                key_added = True
    if not (key_added or key_removed) and not dry:
        print(f"✗ 配置里没有黑名单字段(该固件可能不支持), 只更新本地账本")
    elif dry:
        print("[dry-run] 将 POST /api/%s, 涉及 %d 个 SSID 条目:" % (FILTER_API, len(items)))
        for item in items:
            for k in ("BMACAddresses", "MACAddressBlackList", "BlackMACAddresses"):
                if k in item:
                    print(f"  {item.get('ID', '')}: {item[k]}")
        return

    if not dry:
        http_ok = _post_filter(sess, items)
        # 回读验证(不信任 POST 200)
        time.sleep(1)
        after = _blocked_mac_set(sess)
        in_router = mac in after
        expect = (action == "add")
        verified = (in_router == expect)
        status = "✓ 已验证" if verified else "⚠ 回读不一致(可能未生效, make block-list 复核)"

        # 本地账本: add → 记(带 reason/时间); remove → 删
        if action == "add":
            bl_state[mac] = {"ip": ip, "label": label, "reason": reason or "未知设备手动拉黑",
                             "blocked_at": time.strftime("%m-%d %H:%M")}
        else:
            bl_state.pop(mac, None)
        _save_blocklist(bl_state)
        print(f"{status} {('拉黑' if action == 'add' else '解除')} {ip or target}({mac}) "
              f"router={'在黑名单' if in_router else '不在'} http={http_ok}")
        if action == "add" and not verified:
            print("  未验证生效时先别走远——make unblock 解除, 或路由器页面手动确认")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["add", "remove", "list"])
    ap.add_argument("target", nargs="?", default="")
    ap.add_argument("--reason", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd in ("add", "remove") and not a.target:
        ap.error("add/remove 需要目标 IP 或 MAC")
    cmd(a.cmd, a.target, a.reason, a.force, a.dry_run)
