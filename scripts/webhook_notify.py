#!/usr/bin/env python3
"""webhook_notify.py — 推一条文本到 webhook(钉钉自定义机器人 / 企业微信 / Server酱)。

移植 work/sale/daoye-engine/daoye_engine/notify.py 的零依赖实现(纯 urllib, 容器内可跑):
  - 钉钉「加签」安全设置: URL 追加 timestamp(ms)+sign,
    sign = URL编码( base64( HMAC-SHA256(secret, f"{ts}\\n{secret}") ) )
  - 钉钉/企微失败也回 HTTP 200, 成功必须看 body 里 errcode==0(或 Server酱 code==0)
  - 未配置时 configured()=False, 由调用方决定静默跳过

配置(仓库根 .env 或环境变量; 真环境变量优先):
  WEBHOOK_URL     机器人 webhook(钉钉: https://oapi.dingtalk.com/robot/send?access_token=...)
  WEBHOOK_SECRET  可选。钉钉安全设置选「加签」时填 SEC 开头那串; 选「自定义关键词」则留空
                  (关键词模式建议把 "lume-lan" 填进机器人关键词, 告警文案里都带它)

用法(命令行自测): python3 scripts/webhook_notify.py "测试消息"
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")
URL_KEY = "WEBHOOK_URL"
SECRET_KEY = "WEBHOOK_SECRET"


def _creds():
    """真环境变量 > 仓库根 .env。返回 (url, secret)。"""
    vals = {}
    try:
        for line in open(ENV, encoding="utf-8"):
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    for k in (URL_KEY, SECRET_KEY):
        if os.environ.get(k):
            vals[k] = os.environ[k]
    return vals.get(URL_KEY, "").strip(), vals.get(SECRET_KEY, "").strip()


def configured() -> bool:
    url, _ = _creds()
    return bool(url)


def _sign(url: str, secret: str) -> str:
    """钉钉加签(只认 oapi.dingtalk.com; 企微/Server酱不校验 sign, 原样返回)。"""
    if not secret or "oapi.dingtalk.com" not in url:
        return url
    ts = str(round(time.time() * 1000))
    digest = hmac.new(secret.encode("utf-8"), f"{ts}\n{secret}".encode("utf-8"),
                      hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}timestamp={ts}&sign={sign}"


def send_text(text: str, timeout: float = 15.0):
    """推一段文本。返回 (ok, detail)。未配置 → (False, 说明), 调用方决定要不要当错误。"""
    url, secret = _creds()
    if not url:
        return False, "未配置 WEBHOOK_URL(.env 或环境变量), 跳过推送"
    url = _sign(url, secret)
    payload = json.dumps(
        {"msgtype": "text", "text": {"content": text}},
        ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return False, f"推送失败: {type(e).__name__}: {e}"
    # HTTP 200 ≠ 成功: 钉钉/企微 errcode, Server酱 code, 都要 ==0
    try:
        d = json.loads(body.strip() or "{}")
    except (ValueError, TypeError):
        d = {}
    for f in ("errcode", "code"):
        if f in d and d[f] not in (0, "0", None):
            return False, f"webhook 拒绝: {f}={d[f]} {d.get('errmsg') or d.get('message') or ''}"
    return True, "已推送"


if __name__ == "__main__":
    text = " ".join(sys.argv[1:]) or "lume-lan webhook 测试: 能收到这条说明配好了"
    ok, detail = send_text(text)
    print(("✓ " if ok else "✗ ") + detail)
    sys.exit(0 if ok else 1)
