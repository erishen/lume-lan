#!/usr/bin/env python3
"""router_fetch.py — 从华为 WS5102(HiLink CAT) 后台抓路由器身份 + 终端列表/流量，写 .data/router.json。

已逆向并验证的协议(固件 WS5102-10 SOC1200, 2026-10):
  1. 免密身份层: GET /api/system/deviceinfo → 型号/序列号/OUI(无需登录)。
  2. 登录(简单 SHA256 路径, 与网页 SCRAM 路径的限流计数相互独立):
     a. GET  /html/index.html        → 会话 cookie + meta csrf_param/csrf_token
     b. POST /api/system/user_login   data={UserName,Password,LoginFlag:1},
        其中 Password = SHA256( user + base64(SHA256(pw)) + csrf_param + csrf_token )
        成功 → errorCategory:"ok" + level, 会话升级。
  3. 终端/流量: GET /api/system/HostInfo → 顶层即 list(全部 DHCP 记录, 含历史),
     关键字段: IPAddress/MACAddress/HostName/DevBrands(厂商,路由器识别)/
     Active(在线)/WlanActive/IsGuest/TxKBytes/RxKBytes(累计)/UpRate/DownRate/
     AccessRecord(最后活跃)。
     厂商只取路由器 DevBrands(权威); 识别不出则留空, 由 HostName 兜底展示。

所有请求必须带响应头 _ResponseFormat:JSON + X-Requested-With, 且带会话 cookie,
否则 CAT 会「隐藏」登录态端点返回 404(这是早期 curl 直连全 404 的根因)。

输出(.data/router.json):
  {
    "router": {host,model,friendly,serial,router_type,manufacturer_oui,ok,fetched_at},
    "login":  {ok,method:"sha256_simple",level,error},
    "terminal_source": "api/system/HostInfo",
    "router_devices": [ {ip,mac,hostname,vendor,rx_bytes,tx_bytes,up_rate,
                         down_rate,online,wired,guest,last_seen} ],
    "probe": [...]
  }

用法: python3 scripts/router_fetch.py   (make router-fetch 亦可)
优雅降级: 未填密码 / 登录失败 → router_devices 为空 + login.ok=false,
deviceinfo 身份层仍正常输出(前端只显示路由器身份 + 登录失败提示, 不影响 arp 快照)。
"""
import base64
import hashlib
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
    """读 .env(无第三方依赖; shell env 优先)。"""
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
    for k in ("ROUTER_HOST", "ROUTER_USER", "ROUTER_PASS"):
        if os.environ.get(k):
            vals[k] = os.environ[k]
    return vals


class CatSession:
    """带会话 cookie 的 CAT 客户端。cookie 用 dict(同名覆盖, 保留最新登录态);
    每次请求带 _ResponseFormat:JSON, 否则未登录态 /api/* 被 404 隐藏。"""

    def __init__(self, base: str):
        self.base = base
        self.cookies = {}

    def _req(self, method: str, path: str, body: bytes = None,
             timeout: int = 15) -> tuple:
        """返回 (status, raw_text)。"""
        url = self.base + path
        req = urllib.request.Request(
            url, data=body, method=method,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "_ResponseFormat": "JSON",
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                               "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"),
            },
        )
        if self.cookies:
            req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items()))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                for sc in r.headers.get_all("Set-Cookie") or []:
                    self._absorb(sc)
                return r.getcode(), raw
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            for sc in e.headers.get_all("Set-Cookie") or []:
                self._absorb(sc)
            return e.code, raw
        except Exception as e:
            return -1, f"EXC {e}"

    def _absorb(self, set_cookie: str) -> None:
        kv = set_cookie.split(";")[0]
        if "=" in kv:
            k, v = kv.split("=", 1)
            self.cookies[k.strip()] = v.strip()

    def get_json(self, path: str) -> tuple:
        code, raw = self._req("GET", path)
        try:
            return code, json.loads(raw)
        except Exception:
            return code, {"_raw": raw[:300]}

    def post_json(self, path: str, body: dict) -> tuple:
        code, raw = self._req("POST", path, json.dumps(body).encode())
        try:
            return code, json.loads(raw)
        except Exception:
            return code, {"_raw": raw[:300]}

    def csrf(self) -> dict:
        """GET /html/index.html → 会话 cookie + meta csrf(路由器每次刷新值)。"""
        code, raw = self._req("GET", "/html/index.html")
        meta = {}
        for m in re.finditer(
                r'<meta\s+name="(csrf_param|csrf_token)"\s+content="([^"]*)"', raw):
            meta[m.group(1)] = m.group(2)
        return {"code": code, "csrf": {
            "csrf_param": meta.get("csrf_param", ""),
            "csrf_token": meta.get("csrf_token", "")}}

    def login(self, user: str, pw: str) -> dict:
        """简单 SHA256 登录(与网页 SCRAM 独立计数, 不会被其限流锁拖累)。
        返回 {ok, level, error}。"""
        c = self.csrf()
        if c["code"] != 200 or not c["csrf"]["csrf_param"]:
            return {"ok": False, "level": 0,
                    "error": f"取不到 csrf(html/index.html http={c['code']})"}
        cp, ct = c["csrf"]["csrf_param"], c["csrf"]["csrf_token"]
        # 与前端等价: Password = SHA256(user + b64(sha256(pw)) + csrf_param + csrf_token)
        inner = hashlib.sha256(pw.encode()).hexdigest()
        b64 = base64.b64encode(inner.encode()).decode()
        pwd = hashlib.sha256((user + b64 + cp + ct).encode()).hexdigest()
        code, rj = self.post_json("/api/system/user_login", {
            "data": {"UserName": user, "Password": pwd, "LoginFlag": 1},
            "csrf": c["csrf"],
        })
        if isinstance(rj, dict) and rj.get("errorCategory") == "ok":
            return {"ok": True, "level": int(rj.get("level") or 0), "error": ""}
        err = f"http={code} {json.dumps(rj, ensure_ascii=False)[:160]}" \
            if isinstance(rj, dict) else f"http={code}"
        return {"ok": False, "level": 0, "error": err}

    def hostinfo(self) -> tuple:
        return self.get_json("/api/system/HostInfo")


def fetch_deviceinfo(sess: CatSession, host: str, fetched_at: int, probe: list) -> dict:
    """免密身份层: GET /api/system/deviceinfo(未登录也开放)。"""
    code, rj = sess.get_json("/api/system/deviceinfo")
    probe.append({"endpoint": "api/system/deviceinfo", "method": "GET",
                  "auth": "none", "status": code,
                  "ok": bool(isinstance(rj, dict) and "SerialNumber" in rj)})
    short = host[7:] if host.startswith("http") else host
    if not isinstance(rj, dict) or "SerialNumber" not in rj:
        return {"host": short, "fetched_at": fetched_at, "ok": False,
                "note": f"deviceinfo 取不到(http={code})"}
    cust = rj.get("custinfo") or {}
    return {
        "host": short, "fetched_at": fetched_at, "ok": True,
        "model": cust.get("CustDeviceName") or rj.get("DeviceName") or "",
        "friendly": cust.get("CustZHFriendlyName") or cust.get("CustENFriendlyName") or "",
        "serial": rj.get("SerialNumber", ""),
        "router_type": rj.get("RouterType", ""),
        "manufacturer_oui": rj.get("ManufacturerOUI", ""),
    }


def extract_hostinfo(payload) -> list:
    """HostInfo(顶层即 list)→ 归一化 [{ip,mac,hostname,vendor,rx_bytes,tx_bytes,
    up_rate,down_rate,online,wired,guest,last_seen}]。在线优先, 其次按下载量。"""
    seq = payload if isinstance(payload, list) else None
    if not seq:
        return []
    out = []
    for d in seq:
        if not isinstance(d, dict):
            continue
        mac = str(d.get("MACAddress") or "").lower()
        # 显示名优先级: 用户手写备注(ActualName, 由终端管理页「设备改名/备注」
        # 经 changedevicename 写入, 最权威) > 设备自报主机名(HostName) > 空。
        host = str(d.get("ActualName") or "").strip()
        if not host:
            hn = str(d.get("HostName") or "").strip()
            host = "" if re.fullmatch(r"未知设备-[0-9A-Fa-f]{4}", hn) else hn
        # 厂商只信路由器 DevBrands(权威); 识别不出则留空, 不臆造。
        vendor = str(d.get("DevBrands") or "").strip()
        last = ""
        rec = str(d.get("AccessRecord") or "")
        if rec and "#" in rec:
            last = rec.split("#", 1)[0]

        def kbytes(key):
            try:
                return int(float(d.get(key) or 0)) * 1024
            except Exception:
                return 0

        out.append({
            "ip": str(d.get("IPAddress") or ""),
            "mac": mac,
            "hostname": host,
            "vendor": vendor,
            "rx_bytes": kbytes("RxKBytes"),
            "tx_bytes": kbytes("TxKBytes"),
            "up_rate": int(d.get("UpRate") or 0),
            "down_rate": int(d.get("DownRate") or 0),
            "online": bool(d.get("Active")),
            "wired": (not bool(d.get("WlanActive"))) if d.get("WlanActive") in (True, False) else None,
            "guest": bool(d.get("IsGuest")),
            "last_seen": last,
        })
    out.sort(key=lambda x: (not x["online"], -x["rx_bytes"]))
    return out


def _load_last_devices() -> dict:
    """从 .data/router.json 读上次成功的终端数据（登录失败时沿用它，避免清成空）。"""
    try:
        with open(OUT, encoding="utf-8") as f:
            last = json.load(f)
        devs = last.get("router_devices")
        if isinstance(devs, list) and devs:
            return {"devices": devs, "source": last.get("terminal_source", "")}
    except Exception:
        pass
    return {}


# ---- 流量历史(算「近 1 小时增量」, 区分 活跃/挂起中) ----
HIST = os.path.join(ROOT, ".data", "router_traffic_hist.json")
HIST_CAP = 300      # 每 IP 保留的历史点数上限(60s 一循环 ≈ 5 小时, 远超基线所需)


def _load_hist() -> dict:
    """{ip: [{ts, rx, tx}, ...]} 按 ts 升序。"""
    try:
        with open(HIST, encoding="utf-8") as f:
            h = json.load(f)
        return h if isinstance(h, dict) else {}
    except Exception:
        return {}


def _save_hist(hist: dict) -> None:
    os.makedirs(os.path.dirname(HIST), exist_ok=True)
    tmp = HIST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False)
    os.replace(tmp, HIST)


def apply_traffic_deltas(devices: list, fetched_at: int) -> None:
    """给每台设备算 delta_1h(近 ~1h 的流量增量, 字节; null=历史太短未知),
    并把本轮累计值追加进历史。路由器流量计数器是单调累计值, 两点相减即增量。

    基线取「至少 10 分钟前的最近一个点」: 这样数据循环跑 10 分钟起就能区分
    活跃/挂起(心跳级增量 < 1MB), 满 1h 后窗口精确到 1h; 历史太短(未满 10min)
    则返回 None, 前端按「活跃」显示, 避免误判。"""
    MIN_BASELINE_AGE = 600  # 基线点至少要距今 10 分钟
    hist = _load_hist()
    for d in devices:
        ip = str(d.get("ip") or "")
        cur_rx = d.get("rx_bytes") or 0
        cur_tx = d.get("tx_bytes") or 0
        d["delta_1h"] = None
        series = hist.get(ip, []) if ip else []
        if series:
            cutoff = fetched_at - MIN_BASELINE_AGE
            base = [p for p in series if p.get("ts", 0) <= cutoff]
            if base:
                b = base[-1]
                d["delta_1h"] = max(0, cur_rx - b.get("rx", 0)) + max(0, cur_tx - b.get("tx", 0))
        if ip:
            series.append({"ts": fetched_at, "rx": cur_rx, "tx": cur_tx})
            while len(series) > HIST_CAP:
                series.pop(0)
            hist[ip] = series
    _save_hist(hist)


_FAIL_TS = os.path.join(ROOT, ".runtime", "router_login_fail_ts")
LOGIN_COOLDOWN_SECS = 300  # 登录失败后 5min 内跳过尝试(限流冷却期),避免 60s 循环越打锁越紧


def _read_fail_ts() -> int:
    try:
        return int(open(_FAIL_TS, encoding="utf-8").read().strip())
    except Exception:
        return 0


def _write_fail_ts(ts: int) -> None:
    try:
        os.makedirs(os.path.dirname(_FAIL_TS), exist_ok=True)
        open(_FAIL_TS, "w", encoding="utf-8").write(str(ts))
    except Exception:
        pass


def _clear_fail_ts() -> None:
    try:
        os.remove(_FAIL_TS)
    except Exception:
        pass


def main():
    env = load_env()
    host = (env.get("ROUTER_HOST") or "192.168.3.1")
    if not host.startswith("http"):
        host = "http://" + host
    user = env.get("ROUTER_USER") or "admin"
    pw = env.get("ROUTER_PASS") or ""
    fetched_at = int(time.time())

    sess = CatSession(host)
    probe = []
    router = fetch_deviceinfo(sess, host, fetched_at, probe)

    devices, source, login = [], "", {"ok": False, "level": 0, "error": ""}
    stale = False  # 本次是否沿用上次的终端数据

    # 登录冷却: 上次失败后 LOGIN_COOLDOWN_SECS 内不再尝试, 避免 60s 循环把限流锁越打越紧。
    cooldown = (fetched_at - _read_fail_ts()) < LOGIN_COOLDOWN_SECS
    if cooldown:
        wait = LOGIN_COOLDOWN_SECS - (fetched_at - _read_fail_ts())
        login = {"ok": False, "level": 0, "error": "冷却中"}
        last = _load_last_devices()
        if last:
            devices, source = last["devices"], last.get("source") or "api/system/HostInfo"
            stale = True
            login["stale"] = True
            login["stale_note"] = f"登录冷却中(约 {max(0, wait)//60} 分钟)，沿用上次的终端数据"
        probe.append({"endpoint": "api/system/user_login", "method": "SKIP",
                      "auth": "sha256_simple", "status": 0, "ok": False,
                      "note": f"cooldown, retry in ~{max(0, wait)}s"})

    elif pw:
        login = sess.login(user, pw)
        probe.append({"endpoint": "api/system/user_login", "method": "POST",
                      "auth": "sha256_simple",
                      "status": 200 if login["ok"] else 401,
                      "ok": login["ok"],
                      "note": f"level={login['level']}" if login["ok"] else login["error"]})
        if login["ok"]:
            _clear_fail_ts()
            code, payload = sess.hostinfo()
            devices = extract_hostinfo(payload)
            source = "api/system/HostInfo"
            probe.append({"endpoint": "api/system/HostInfo", "method": "GET",
                          "auth": "session", "status": code,
                          "ok": bool(devices),
                          "note": f"{len(devices)} hosts" if devices else f"http={code}"})
        else:
            # 登录失败（限流/密码错）：记冷却时间戳 + 保留上次成功的终端数据
            _write_fail_ts(fetched_at)
            last = _load_last_devices()
            if last:
                devices, source = last["devices"], last.get("source") or "api/system/HostInfo"
                stale = True
                login["stale"] = True
                login["stale_note"] = "本次登录失败，沿用上次的终端列表数据"
            probe.append({"endpoint": "api/system/user_login", "method": "POST",
                          "auth": "cooldown-set", "status": 0, "ok": False,
                          "note": f"失败, {LOGIN_COOLDOWN_SECS}s 内不再尝试"})
    else:
        login = {"ok": False, "level": 0, "error": "未设置 ROUTER_PASS(.env), 跳过登录态抓取"}
        probe.append({"endpoint": "api/system/user_login", "method": "POST",
                      "auth": "sha256_simple", "status": 0, "ok": False,
                      "note": login["error"]})

    # 计算近 1 小时流量增量(区分 活跃/挂起中), 并把本轮累计值记入历史
    apply_traffic_deltas(devices, fetched_at)

    payload_out = {
        "router": router,
        "login": {"ok": login["ok"], "method": "sha256_simple",
                  "level": login.get("level", 0), "error": login.get("error", ""),
                  "stale": stale,
                  "stale_note": login.get("stale_note", "")},
        "terminal_source": source,
        "router_devices": devices,
        "router_active": sum(1 for d in devices
                             if d.get("online")
                             and (d.get("delta_1h") is None or d.get("delta_1h", 0) >= 1048576)),
        "probe": probe,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload_out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT)

    print(f"router.json: deviceinfo ok={router.get('ok')} "
          f"model={router.get('model')} serial={router.get('serial')}")
    if login["ok"]:
        on = sum(1 for d in devices if d["online"])
        withv = sum(1 for d in devices if d["vendor"])
        print(f"login: ok(level={login.get('level')}) terminal=HostInfo "
              f"hosts={len(devices)} online={on} 有厂商={withv}")
    elif stale and devices:
        print(f"login: {login.get('error') or '失败'} → 沿用上次 {len(devices)} 台终端数据")
    else:
        print(f"login: 失败({login.get('error')}) → router_devices 为空, 仅保留身份层")


if __name__ == "__main__":
    main()
