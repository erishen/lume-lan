#!/usr/bin/env python3
"""router_fetch.py — 从路由器后台(华为 WS5102 / HiLink)抓设备与流量信息，写 .data/router.json。

分层(优雅降级):
  1. 免密开放:  GET /api/system/deviceinfo → 路由器身份(型号/序列号/频段/厂商)。
                这一层无密码、无登录也能拿,失败不影响整体。
  2. 登录态(需 .env 的 ROUTER_PASS): 终端列表 + 每设备流量。HiLink 私有端点名
                各固件不同、且请求体经 cat_enc.js 加密,无法靠猜。策略:
                  - 若 .env 显式设了 ROUTER_TERMINAL_API / ROUTER_TRAFFIC_API,按它打;
                  - 否则对一批候选端点逐个 GET/POST 探测,把「返回 200 且能解析出设备」
                    的记进结果,并在日志提示"把该端点回填到 .env 锁定"。
                取不到就跳过,arp 快照仍由 scan_lan.py 正常产出。

输出(.data/router.json):
  {
    "router": {"host","model","serial","band","manufacturer","fetched_at"},
    "terminal_source": "<端点或''>",
    "router_devices": [ {"ip","mac","hostname","rx_bytes","tx_bytes","online","rssi"} ],
    "probe": [ {"endpoint","method","status","ok","note"} ]   # 探测记录,便于你确认
  }

用法: python3 scripts/router_fetch.py   (make router-fetch 亦可)
"""
import json
import os
import re
import time
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")
OUT = os.path.join(ROOT, ".data", "router.json")


def load_env() -> dict:
    """读 .env 成 dict(无第三方依赖;变量已设则 shell env 优先)。"""
    vals = {}
    try:
        for line in open(ENV, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    for k in ("ROUTER_HOST", "ROUTER_USER", "ROUTER_PASS",
              "ROUTER_TERMINAL_API", "ROUTER_TRAFFIC_API"):
        if os.environ.get(k):
            vals[k] = os.environ[k]
    return vals


def http(method: str, url: str, body: bytes = None, timeout: int = 6) -> tuple:
    """返回 (status_code, body_bytes)。网络/超时 → (-1, b'')。"""
    req = urllib.request.Request(
        url, data=body, method=method,
        headers={"Content-Type": "application/json",
                 "User-Agent": "lume-lan-router-fetch/0.1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode(), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception:
        return -1, b""


def as_text(raw: bytes) -> str:
    """路由器响应可能是明文 JSON,也可能是 HiLink 的 hex 串(cat 协议)。两种都解。"""
    if not raw:
        return ""
    txt = raw.decode("utf-8", "replace").strip()
    # HiLink 部分端点返回 hex:先试当 hex 解开取 UTF-8
    if re.fullmatch(r"[0-9a-fA-F]+", txt or "") and len(txt) % 2 == 0:
        try:
            return bytes.fromhex(txt).decode("utf-8", "replace")
        except Exception:
            return txt
    return txt


def get_json(method: str, url: str, body: bytes = None) -> tuple:
    """返回 (ok:bool, payload, status)。payload 解析失败为 None。"""
    code, raw = http(method, url, body)
    txt = as_text(raw)
    payload = None
    if code in (200, 201, 204) and txt:
        try:
            payload = json.loads(txt)
        except Exception:
            payload = None
    ok = payload is not None
    return ok, payload, code


def fetch_deviceinfo(host: str, fetched_at: int) -> dict:
    """免密开放端点:路由器身份。"""
    _, payload, code = get_json("GET", f"{host}/api/system/deviceinfo")
    if not payload or not isinstance(payload, dict):
        return {"host": host[7:], "fetched_at": fetched_at, "ok": False,
                "note": f"deviceinfo 取不到(http={code})"}
    cust = payload.get("custinfo") or {}
    return {
        "host": host[7:],
        "fetched_at": fetched_at,
        "ok": True,
        "model": cust.get("CustDeviceName") or payload.get("DeviceName") or "",
        "friendly": cust.get("CustZHFriendlyName") or cust.get("CustENFriendlyName") or "",
        "serial": payload.get("SerialNumber", ""),
        "router_type": payload.get("RouterType", ""),
        "manufacturer_oui": payload.get("ManufacturerOUI", ""),
    }


def extract_devices(payload) -> list:
    """把「终端列表」类响应归一成 [{ip,mac,hostname,rx,tx,online}]。

    HiLink 终端表常见键:iptableList / dhcpclinetlist / terminalList,
    字段可能是 ip/mac/onlinestate/txbytes/... ——尽力而为,取不到就留空。
    """
    if not isinstance(payload, (dict, list)):
        return []
    seq = None
    if isinstance(payload, dict):
        for key in ("iptableList", "iptable", "dhcpclinetlist", "dhcpclinet",
                    "terminalList", "deviceList", "iptstatList"):
            if key in payload and isinstance(payload[key], list):
                seq = payload[key]
                break
        if seq is None:
            # 有些固件把列表直接放顶层 map
            for v in payload.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    seq = v
                    break
    elif isinstance(payload, list):
        seq = payload
    if not seq:
        return []
    out = []
    for d in seq:
        if not isinstance(d, dict):
            continue
        out.append({
            "ip": str(d.get("ip") or d.get("ipaddr") or d.get("ipaddress") or ""),
            "mac": str(d.get("mac") or d.get("macaddr") or "").lower(),
            "hostname": str(d.get("hostname") or d.get("devicename") or d.get("name") or ""),
            "rx_bytes": int(d.get("rxbytes") or d.get("rx") or 0),
            "tx_bytes": int(d.get("txbytes") or d.get("tx") or 0),
            "rssi": d.get("rssi") if "rssi" in d else None,
            "online": bool(d.get("online") if "online" in d else d.get("onlinestate")),
            "wired": (d.get("interface") or d.get("wired") in ("1", 1, "true", True)) if ("interface" in d or "wired" in d) else None,
        })
    return out


# 候选终端/流量端点(华为 HiLink 常见;逐个探,命中即用于结果)
CANDIDATES = [
    ("GET", "api/dhcp/dhcpclinet"),
    ("GET", "api/dhcp/dhcpclinetlist"),
    ("GET", "api/iptable/iptablelist"),
    ("GET", "api/net/traffictable"),
    ("GET", "api/dhcp/dhcpipstat"),
    ("POST", "api/dhcp/dhcpclinet"),
    ("POST", "api/iptable/iptable"),
    ("POST", "api/dhcp/monitor/dhcpclinet"),
]


def probe_terminal(host: str, user: str, pw: str, explicit: str) -> tuple:
    """返回 (devices:list, source_endpoint:str, probe:records)。"""
    results = []
    hits = []
    base = explicit if explicit else ""
    cands = [( "GET", base)] if base else CANDIDATES
    # 登录态端点:先试无鉴权 GET(部分固件读接口开放),再带 Basic Auth 试一次
    for method, path in cands:
        url = f"{host}/{path}"
        body = b""
        if method == "POST":
            body = json.dumps({"iptable": {"iptable": {"opType": 0}}}).encode()
        for auth in (None, "basic"):
            hdr = None
            if auth == "basic" and pw:
                import base64
                hdr = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
            if hdr:
                code, raw = http(method, url, body, timeout=6)
                txt = as_text(raw)
                payload = None
                try:
                    payload = json.loads(txt)
                except Exception:
                    pass
                ok = payload is not None
            else:
                ok, payload, code = get_json(method, url, body)
            dev = extract_devices(payload)
            note = f"{code}" + (f" · {len(dev)} device" if dev else "")
            results.append({"endpoint": "/" + path, "method": method,
                            "auth": auth, "status": code, "ok": bool(dev),
                            "note": note})
            if dev:
                hits.append((dev, "/" + path))
            if dev:
                break
        if hits:
            break
    devices = hits[0][0] if hits else []
    source = hits[0][1] if hits else ""
    return devices, source, results


def main():
    env = load_env()
    host = env.get("ROUTER_HOST") or "http://192.168.3.1"
    if not host.startswith("http"):
        host = "http://" + host
    user = env.get("ROUTER_USER") or "admin"
    pw = env.get("ROUTER_PASS") or ""
    t_api = env.get("ROUTER_TERMINAL_API") or ""
    f_api = env.get("ROUTER_TRAFFIC_API") or ""
    fetched_at = int(time.time())

    router = fetch_deviceinfo(host, fetched_at)
    devices, source, probe = probe_terminal(host, user, pw, t_api or f_api)
    if f_api and not source:
        # 流量端点单独探(可能比终端端点宽松)
        d2, src2, p2 = probe_terminal(host, user, pw, f_api)
        for d in d2:
            d["rx_bytes"] = d.get("rx_bytes") or 0
        devices, source = d2, src2
        probe += p2

    payload = {
        "router": router,
        "terminal_source": source,
        "router_devices": devices,
        "probe": probe,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT)

    # 人读摘要
    print(f"router.json: deviceinfo ok={router.get('ok')} "
          f"model={router.get('model')} serial={router.get('serial')}")
    print(f"terminal: source={source or '(无)'} devices={len(devices)}")
    if not source and probe:
        ok_any = [p for p in probe if p["ok"]]
        if ok_any:
            print("可用端点: " + ", ".join(p["endpoint"] for p in ok_any))
        else:
            print("未命中终端端点。候选探测记录见 probe 字段;")
            print("  请在本机浏览器登录后台,看 Network 里『终端管理/流量』实际请求,")
            print("  把端点填到 .env 的 ROUTER_TERMINAL_API(形如 api/dhcp/dhcpclinet)。")


if __name__ == "__main__":
    main()
