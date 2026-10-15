#!/usr/bin/env python3
"""scan_lan.py — 快照当前局域网设备，写 .data/devices.json（Lume 服务端只读该文件）。

lume-core 没有 exec/子进程内置，所以「取设备」的真实系统调用（arp / ping）
放在宿主脚本里跑，Lume 侧负责把快照做成 HTTP API + React 界面。

两种模式（env 控制）：
  - 默认：解析 `arp -a` 现成表（秒级，只反映「已连接/近期通信过的」设备）。
  - LAN_SWEEP=1：在网关所在 /24 网段做 ping sweep（`ping -c 1 -t 1`，并发），
    能捞出「在线但 arp 表里还没」的设备；较慢（默认 253 个 IP，约 10~30s）。

输出（.data/devices.json）：
  {
    "gateway": "192.168.3.1",
    "subnet": "192.168.3.0/24",
    "scanned_at": 1700000000,
    "sweep": true,
    "devices": [ {"ip","mac","iface","live","source"} ]
  }
"""
import json
import os
import re
import subprocess
import time
import concurrent.futures
from ipaddress import ip_address

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, ".data", "devices.json")
SWEEP = os.environ.get("LAN_SWEEP") == "1"


def norm_mac(m: str) -> str:
    """c4:6:83:71:74:4 → c4:68:37:71:74:04（每段补零）。"""
    parts = [p for p in m.split(":") if p != ""]
    if not parts:
        return ""
    return ":".join(p.zfill(2) for p in parts)


def is_noise(ip: str, mac: str, iface: str = "") -> bool:
    """过滤非真实设备：
    - 169.254/16 link-local
    - 广播 / 全 0 MAC
    - macOS 虚拟 bridge 接口（bridge100/106/... 是本机自分配子网，非真实 LAN 设备）
    - x.x.x.0 网络地址（ARP 表会列出虚拟子网的网络位，非可连设备）"""
    if ip.startswith("169.254."):
        return True
    mm = mac.lower()
    if mm in ("", "ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00"):
        return True
    if iface and iface.startswith("bridge"):
        return True
    if ip.endswith(".0"):
        return True
    return False


def _gateway_from_arp() -> str:
    """兜底：macOS arp -a 里网关那条带 ifscope 标记（如
    "? (192.168.3.1) at c4:6:8:… on en0 ifscope [ethernet]"）。

    注意 ifscope 会有多条——广播条目（.0，MAC ff:ff:ff:ff:ff:ff）与真网关
    （MAC 是真实单播）。route 被 OS 拦时用它：取「MAC 非广播」的首条。"""
    mac_re = re.compile(r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})")
    try:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            if "ifscope" not in line:
                continue
            m_mac = mac_re.search(line)
            if m_mac and m_mac.group(1).lower() == "ff:ff:ff:ff:ff:ff":
                continue  # 广播条目不是网关
            m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)", line) or re.search(
                r"^\s*(\d+\.\d+\.\d+\.\d+)\s", line
            )
            if m:
                return m.group(1)
    except Exception:
        pass
    return ""


def detect_gateway() -> str:
    """从系统默认路由抓网关 IP（跨 macOS/Linux，不依赖 ip 命令）。

    顺序：route get default（宿主终端正常）→ arp ifscope 条目（受限环境）
    → 兜底 10.0.0.1。
    """
    try:
        out = subprocess.run(
            ["route", "get", "default"], capture_output=True, text=True, timeout=5
        ).stdout
        m = re.search(r"gateway:\s*(\d+\.\d+\.\d+\.\d+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    gw = _gateway_from_arp()
    if gw:
        return gw
    return "10.0.0.1"


def arp_snapshot() -> list:
    """解析 `arp -a`（macOS）或 `arp -a`（Linux 格式不同，尽力而为）。"""
    devices = {}
    try:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    # macOS:  ? (192.168.3.1) at c4:6:83:71:74:4 on en0 ifscope [ethernet]
    #         192.168.3.1 at (incomplete) on en0
    # Linux : 192.168.3.1 at c4:68:37:71:74:04 [ether] on en0
    mac_re = re.compile(r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})")
    for line in out.splitlines():
        m_ip = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)", line) or re.search(
            r"^\s*(\d+\.\d+\.\d+\.\d+)\s+at", line
        )
        if not m_ip:
            continue
        ip = m_ip.group(1)
        m_mac = mac_re.search(line)
        m_if = re.search(r"\bon\s+(\w+)", line)
        incomplete = "incomplete" in line or "<incomplete>" in line
        devices[ip] = {
            "ip": ip,
            "mac": "" if incomplete else norm_mac(m_mac.group(1)) if m_mac else "",
            "iface": m_if.group(1) if m_if else "",
            "live": not incomplete,
            "source": "arp",
        }
    return list(devices.values())


def ping_one(ip: str) -> dict:
    """单个 IP 探活：ping -c 1 -t 1（macOS/Linux 通吃），1.5s 硬超时。"""
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-t", "1", "-W", "1", ip],
            capture_output=True, timeout=1.5,
        )
        live = r.returncode == 0
    except Exception:
        live = False
    return {"ip": ip, "mac": "", "iface": "", "live": live, "source": "sweep"}


def sweep_subnet(gw: str) -> list:
    """网关所在 /24 全段并发 ping，捞出在线但 arp 表没有的设备。"""
    try:
        from ipaddress import IPv4Network
        net24 = IPv4Network(f"{detect_network(gw)}/24")
    except Exception:
        return []
    ips = [str(x) for x in net24.hosts()]  # 去掉网络位/广播位
    out = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=48) as ex:
        for d in ex.map(ping_one, ips):
            if d["live"]:
                out.append(d)
    return out


def main():
    gw = detect_gateway()
    devices = arp_snapshot()
    sweep = SWEEP
    if sweep:
        swn = sweep_subnet(gw)
        have = {d["ip"] for d in devices}
        for d in swn:
            if d["ip"] not in have:
                devices.append(d)
        # sweep 命中的 IP，若 arp 里没有就补 live
        for d in devices:
            if any(s["ip"] == d["ip"] and s["live"] for s in swn):
                d["live"] = True
    devices.sort(key=lambda d: ip_address(d["ip"]))
    # 网关本身标记
    for d in devices:
        if d["ip"] == gw:
            d["gateway"] = True
    # 过滤噪音条目（link-local / 广播 / 全 0 / 虚拟 bridge 接口 / .0 网络位），但保留网关
    devices = [
        d for d in devices
        if not (is_noise(d["ip"], d.get("mac", ""), d.get("iface", "")) and not d.get("gateway"))
    ]
    payload = {
        "gateway": gw,
        "subnet": f"{detect_network(gw)}/24",
        "scanned_at": int(time.time()),
        "sweep": sweep,
        "devices": devices,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT)  # 原子替换，Lume 侧读到的是完整文件
    print(f"wrote {OUT}: {len(devices)} device(s) (gateway {gw}, sweep={sweep})")


def detect_network(gw: str) -> str:
    """网关所在 /24 的网络地址（192.168.3.1 → 192.168.3.0）。"""
    import struct

    packed = struct.pack(">I", int(ip_address(gw)))
    network = struct.unpack(">I", packed[:2] + b"\x00\x00")[0]
    return str(ip_address(network))


if __name__ == "__main__":
    main()
