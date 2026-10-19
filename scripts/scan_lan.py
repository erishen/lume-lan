#!/usr/bin/env python3
"""scan_lan.py — 快照当前局域网设备，写 .data/devices.json（Lume 服务端只读该文件）。

lume-core 没有 exec/子进程内置，所以「取设备」的真实系统调用（arp / ping）
放在宿主脚本里跑，Lume 侧负责把快照做成 HTTP API + React 界面。

两种模式（env 控制）：
  - 默认：解析 `arp -a` 现成表（秒级，只反映「已连接/近期通信过的」设备）。
  - LAN_SWEEP=1：在网关所在 /24 网段做 ping sweep（平台感知的 ping 参数，并发），
    能捞出「在线但 arp 表里还没」的设备；较慢（默认 253 个 IP，约 10~30s）。

输出（.data/devices.json）：
  {
    "gateway": "192.168.1.1",
    "subnet": "192.168.1.0/24",
    "scanned_at": 1700000000,
    "sweep": true,
    "devices": [ {"ip","mac","iface","live","source"} ]
  }
"""
import json
import os
import re
import subprocess
import sys
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


def _is_virtual_iface(name: str) -> bool:
    """虚拟/隧道接口名（macOS Docker bridge100-108、VPN utun/tun/tap/ppp/ipsec/awdl、
    Linux docker0/veth/wg/tailscale 等）。真实 LAN 网关只会出现在物理网卡
    （以太网/Wi-Fi）上；VPN/隧道瞬时接管默认路由时网关会变成虚拟口上的假 IP
    （如 74.125.250.129），这种网关一律不采信，否则看板网关与扫描网段会被带偏。"""
    n = (name or "").lower()
    return n.startswith(("utun", "tun", "tap", "ppp", "ipsec", "awdl",
                         "bridge", "docker", "veth", "wg", "tailscale"))


def _host_ipv4s() -> dict:
    """{接口: IPv4} — 本机各接口地址（macOS/Linux ifconfig 兼容：inet 与 inet addr: 两种格式）。"""
    ips: dict = {}
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ips
    cur = None
    for line in out.splitlines():
        m = re.match(r"^(\S+):", line)
        if m:
            cur = m.group(1)
            continue
        m = re.search(r"\binet\s+(?:addr:)?(\d+\.\d+\.\d+\.\d+)", line)
        if m and cur:
            ips[cur] = m.group(1)
    return ips


def _same_lan(ip: str, host_ips: dict, prefix: int = 16) -> bool:
    """候选网关是否与本机任一接口 IP 同网段（默认 /16 宽松校验：家庭/办公网
    网关必然和本机同网段；VPN/隧道假网关如 74.125.x.x 必然对不上 192.168.x.x）。"""
    try:
        cand_net = detect_network(ip, prefix)
    except Exception:
        return False
    for hip in host_ips.values():
        try:
            if detect_network(hip, prefix) == cand_net:
                return True
        except Exception:
            continue
    return False


def _gateway_from_arp(host_ips: dict = None) -> str:
    """兜底：macOS arp -a 里网关那条带 ifscope 标记（如
    "? (192.168.1.1) at c4:6:8:… on en0 ifscope [ethernet]"）。

    注意 ifscope 会有多条——广播条目（.0，MAC ff:ff:ff:ff:ff:ff）与真网关
    （MAC 是真实单播）。route 被 OS 拦时用它：取「物理接口上、与本机同网段、
    MAC 非广播」的首条（跳过 Docker/VPN 虚拟接口的 ifscope 条目，如 bridge108
    上 74.125.250.129 那种；host_ips 拿不到时退化为只跳过虚拟接口）。"""
    mac_re = re.compile(r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})")
    try:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            if "ifscope" not in line:
                continue
            m_mac = mac_re.search(line)
            if m_mac and m_mac.group(1).lower() == "ff:ff:ff:ff:ff:ff":
                continue  # 广播条目不是网关
            m_if = re.search(r"\bon\s+(\w+)", line)
            iface = m_if.group(1) if m_if else ""
            if _is_virtual_iface(iface):
                continue
            m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)", line) or re.search(
                r"^\s*(\d+\.\d+\.\d+\.\d+)\s", line
            )
            if m:
                cand = m.group(1)
                if host_ips and not _same_lan(cand, host_ips):
                    continue
                return cand
    except Exception:
        pass
    return ""


def detect_gateway() -> str:
    """从系统默认路由抓网关 IP（跨 macOS/Linux，不依赖 ip 命令）。

    顺序：route get default（且出口为物理接口、网关与本机同网段）
    → arp ifscope 条目（受限环境）→ 兜底 10.0.0.1。

    加固点：Docker/VPN 的虚拟接口（bridge100-108、utun 等）会瞬时接管默认路由，
    route get default 会返回虚拟口上的假网关（如 74.125.250.129），直接信任会
    把看板网关/扫描网段带偏，所以「虚拟接口」与「非同网段」的网关一律不采信。
    """
    host_ips = _host_ipv4s()
    try:
        out = subprocess.run(
            ["route", "get", "default"], capture_output=True, text=True, timeout=5
        ).stdout
        m_gw = re.search(r"gateway:\s*(\d+\.\d+\.\d+\.\d+)", out)
        m_if = re.search(r"interface:\s*(\S+)", out)
        gw = m_gw.group(1) if m_gw else ""
        iface = m_if.group(1) if m_if else ""
        if gw and iface and not _is_virtual_iface(iface) and (not host_ips or _same_lan(gw, host_ips)):
            return gw
        if gw:
            print(f"  [scan] 默认路由出口 {iface or '?'} 上的网关 {gw} 不可信(虚拟接口/非同网段), 走 arp 兜底")
    except Exception:
        pass
    gw = _gateway_from_arp(host_ips)
    if gw:
        return gw
    return "10.0.0.1"


def detect_host_ip() -> str:
    """本机在局域网里的 IP。配 AdGuard DNS「首选」要指向它。

    优先取「默认路由出口」上的地址，但要排除 VPN/隧道/虚拟接口——VPN 或多网卡
    接管默认路由时，出口可能是 utun 之类，其地址不是家庭 LAN 的物理地址（把 DNS
    指向它会不可达）。顺序：

    1. `route get default` 的 interface 若为物理接口 → 用 ifconfig 取该口 IPv4；
    2. 否则退回「与本机可信网关同网段」的物理接口 IPv4（复用 detect_gateway 的
       虚拟接口/同网段校验）；
    3. 仍拿不到再退到 UDP socket 连外网取源 IP（可能是隧道地址，仅作兜底）；
    4. 兜底 127.0.0.1。
    """
    import socket

    host_ips = _host_ipv4s()

    # 1) 默认路由出口接口（非虚拟）上的地址
    try:
        out = subprocess.run(
            ["route", "get", "default"], capture_output=True, text=True, timeout=5
        ).stdout
        m_if = re.search(r"interface:\s*(\S+)", out)
        iface = m_if.group(1) if m_if else ""
        if iface and not _is_virtual_iface(iface) and host_ips.get(iface):
            return host_ips[iface]
    except Exception:
        pass

    # 2) 与本机可信网关同网段的物理接口地址（VPN 接管默认路由时命中这里）
    try:
        gw = detect_gateway()
        if gw:
            for iface, ip in host_ips.items():
                if _is_virtual_iface(iface):
                    continue
                if _same_lan(gw, {iface: ip}):
                    return ip
    except Exception:
        pass

    # 3) 兜底：连外网时本机会用到的源 IP（不真发包，socket 分配不触发网络）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 53))
        ip = s.getsockname()[0]
        s.close()
        if ip and ip != "127.0.0.1":
            return ip
    except Exception:
        pass
    return "127.0.0.1"


def arp_snapshot() -> list:
    """解析 `arp -a`（macOS）或 `arp -a`（Linux 格式不同，尽力而为）。"""
    devices = {}
    try:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    # macOS:  ? (192.168.1.1) at c4:6:83:71:74:4 on en0 ifscope [ethernet]
    #         192.168.1.1 at (incomplete) on en0
    # Linux : 192.168.1.1 at c4:68:37:71:74:04 [ether] on en0
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


def _ping_args() -> list:
    """按平台构造 ping 探活参数——同名选项在 macOS 与 Linux 上语义不同，
    写死 `-c 1 -t 1 -W 1` 会踩坑：

    - macOS/BSD：`-t 1`（秒，总超时） + `-W 1000`（毫秒，等待应答）
    - Linux/其他：`-W 1`（秒）；不传 `-t`（Linux 的 `-t` 是 TTL，含义不同）
    """
    if sys.platform == "darwin" or sys.platform.startswith(("freebsd", "openbsd", "netbsd")):
        return ["-c", "1", "-t", "1", "-W", "1000"]
    return ["-c", "1", "-W", "1"]


def ping_one(ip: str) -> dict:
    """单个 IP 探活：按平台 ping 一次，1.5s 硬超时。"""
    try:
        r = subprocess.run(
            ["ping", *_ping_args(), ip],
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
    scanned_at = int(time.time())
    payload = {
        "gateway": gw,
        "host_ip": detect_host_ip(),
        "subnet": f"{detect_network(gw)}/24",
        "scanned_at": scanned_at,
        "sweep": sweep,
        # —— 统一数据源状态契约(与 router_fetch/adguard_fetch 一致)——
        # reachable/fetched_at/stale/error: 让前端对任一来源都能独立判「正常/降级/错误」。
        "reachable": True,          # 宿主本地读 arp 表, 能写快照即可达
        "fetched_at": scanned_at,
        "stale": False,
        "error": "",
        "devices": devices,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, OUT)  # 原子替换，Lume 侧读到的是完整文件
    print(f"wrote {OUT}: {len(devices)} device(s) (gateway {gw}, sweep={sweep})")


def detect_network(gw: str, prefix: int = 24) -> str:
    """IP 所在网络地址（默认 /24）。192.168.1.1 → 192.168.1.0。

    旧实现误把后两段清零（192.168.1.1 → 192.168.0.0），导致看板网段显示错误、
    LAN_SWEEP 扫错网段；此处只清零最后一段，prefix 可放宽（如 /16 供同网段校验）。
    """
    from ipaddress import IPv4Network
    return str(IPv4Network(f"{gw}/{prefix}", strict=False).network_address)


if __name__ == "__main__":
    main()
