#!/usr/bin/env python3
"""check_privacy.py — 防「真实局域网标识」再次进入仓库。

背景: 本仓库公开, 但历史上多次出现真实家庭网络信息被写进代码/测试
(2026-10 的 tests/test_scan_lan.py 带进了真实网段 192.168.x / 网关 MAC /
主机 IP; 且有一次「清理」之后新建的文件又把它们带了回来)。

两类规则:
  1) 硬规则(默认, 必须过): 私有网段、公网 IP、真实 MAC 等**格式**类命中。
     这些在文档/示例里应统一用保留地址(192.168.1.0/24、aa:bb:cc:..)。
  2) 本机实况(可选, --local): 拿 .data/ 里**本机真实**的网段/网关/主机 IP/
     MAC 去比, 命中即判泄漏。.data/ 是宿主私有数据, 只在本地跑得出来。

用法:
  python3 scripts/check_privacy.py            # 扫 git 跟踪的文件(硬规则)
  python3 scripts/check_privacy.py --local    # 再叠本机实况比对
  python3 scripts/check_privacy.py --staged   # 只看暂存区(git hook 用)

退出码: 0 = 通过; 1 = 发现疑似泄漏(附文件:行号)。
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 允许的「保留/示例」值 —— 这些出现是正常的, 不算泄漏。
#   192.168.1.x  文档与测试统一用它做示例网段
#   10.x / 172.16-31.x  私有网段中也常用于示例
ALLOWED_IPS_EXACT = {
    "127.0.0.1", "0.0.0.0",            # 回环 / 通配绑定
    "223.5.5.5", "114.114.114.114", "8.8.8.8",   # 公共 DNS(示例备用 DNS)
    "74.125.250.129",                  # 文档里的「公网可达但不在本网段」示例
    "224.0.0.0", "224.0.0.251",        # 组播
    "239.255.255.250",                 # SSDP 组播
    "01:00:5e:7f:ff:fa",               # 组播 MAC 示例
}
ALLOWED_SUBNETS = (
    "192.168.1.",      # 示例网段(README/docker-compose/tests 统一用)
    "192.168.5.",      # 测试用「另一个内网」
    "192.168.0.",      # 文档里说明旧实现 bug 的示例
    "192.168.164.",    # Docker Desktop NAT 网段(Makefile 注释里的已知现象)
    "10.0.0.",         # 测试里做「别的网段」用
    "10.1.1.",         # 测试 default 参数
    "10.8.0.",         # VPN 示例
    "172.17.",         # Docker 默认 bridge 网段
    "127.0.0.",
    "255.255.",        # 子网掩码 / 广播
)
# 允许的示例 MAC(全相同/顺序递增这类明显占位)
ALLOWED_MACS = {
    "aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:0f", "11:22:33:44:55:66",
    "ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00",
    "aa:aa:aa:aa:aa:aa", "cc:cc:cc:cc:cc:cc", "bb:bb:bb:bb:bb:bb",
    "01:00:5e:7f:ff:fa",   # 组播 MAC(SSDP 示例)
}

RE_IPV4 = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")
RE_MAC = re.compile(r"\b([0-9a-fA-F]{1,2})(?::[0-9a-fA-F]{1,2}){5}\b")

# 二进制/生成物不扫
SKIP_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2",
               ".ttf", ".gz", ".zip", ".tar", ".bin")
SKIP_DIRS = ("node_modules/", "vendor/", ".data/", ".runtime/", ".git/")


def is_private(ip: str) -> bool:
    a, b, _, _ = (int(x) for x in ip.split("."))
    return (a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168))


def tracked_files(staged: bool):
    if staged:
        out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                             cwd=ROOT, capture_output=True, text=True).stdout
    else:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                             capture_output=True, text=True).stdout
    for line in out.splitlines():
        line = line.strip()
        if not line or line.endswith(SKIP_SUFFIX):
            continue
        if any(d in line for d in SKIP_DIRS):
            continue
        yield line


def load_local_facts():
    """从 .data/ 取本机真实标识(仅在 --local 时用, 不落日志)。"""
    facts = {"ips": set(), "macs": set(), "net": ""}
    dev = os.path.join(ROOT, ".data", "devices.json")
    try:
        d = json.load(open(dev, encoding="utf-8"))
    except (OSError, ValueError):
        return facts
    for k in ("gateway", "host_ip"):
        v = str(d.get(k) or "").strip()
        if v:
            facts["ips"].add(v)
    net = str(d.get("subnet") or "").strip()      # 如 192.168.1.0/24(示例值, 实际读本机快照)
    if net:
        facts["net"] = net
    for x in d.get("devices", []):
        m = str(x.get("mac") or "").strip().lower()
        if m:
            facts["macs"].add(m)
    return facts


def main(argv):
    staged = "--staged" in argv
    use_local = "--local" in argv
    facts = load_local_facts() if use_local else {"ips": set(), "macs": set(), "net": ""}

    problems = []
    for rel in tracked_files(staged):
        path = os.path.join(ROOT, rel)
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for no, line in enumerate(text.splitlines(), 1):
            # --- IP ---
            for m in RE_IPV4.finditer(line):
                ip = m.group(0)
                octets = [int(x) for x in ip.split(".")]
                if any(o > 255 for o in octets):
                    continue
                if use_local and ip in facts["ips"]:
                    problems.append((rel, no, f"本机真实 IP: {ip}"))
                    continue
                if ip in ALLOWED_IPS_EXACT:
                    continue
                if not is_private(ip):
                    # 子网掩码(255.x)与广播地址不是主机地址, 不算泄漏
                    if ip.startswith("255."):
                        continue
                    # 公网 IP 只在「裸数字」上下文提示; 版本号/端口不会匹配 \b\d+\.\d+\.\d+\.\d+\b
                    problems.append((rel, no, f"疑似公网 IP: {ip}"))
                    continue
                if not ip.startswith(ALLOWED_SUBNETS):
                    problems.append((rel, no, f"非示例私有网段: {ip}"))
            # --- MAC ---
            for m in RE_MAC.finditer(line):
                mac = m.group(0).lower()
                norm = ":".join(p.zfill(2) for p in mac.split(":"))
                if use_local and norm in facts["macs"]:
                    problems.append((rel, no, f"本机真实 MAC: {norm}"))
                elif norm not in ALLOWED_MACS:
                    problems.append((rel, no, f"非示例 MAC: {norm}"))

    if not problems:
        scope = "暂存区" if staged else "已跟踪文件"
        extra = " + 本机实况比对" if use_local else ""
        print(f"✓ 隐私检查通过（{scope}{extra}）")
        return 0

    print("✗ 发现疑似真实局域网标识（仓库公开，请改用 192.168.1.x / aa:bb:cc:.. 等示例值）:",
          file=sys.stderr)
    for rel, no, msg in problems:
        print(f"  {rel}:{no}  {msg}", file=sys.stderr)
    print(f"\n共 {len(problems)} 处。若确属示例，请改用保留地址；"
          f"确需放行的例外请加到 ALLOWED_SUBNETS / ALLOWED_MACS。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
