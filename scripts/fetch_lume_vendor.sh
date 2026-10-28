#!/usr/bin/env sh
# 宿主预下载 Lume 全静态 linux 二进制到 vendor/(供 Docker build 离线 COPY, 绕开 OrbStack
# 容器内 curl 走网关 TLS 被截断 exit 35)。按本机 arch 取对应 static 资产。
#
# 用法: scripts/fetch_lume_vendor.sh [LUME_VERSION] [LUME_REPO]
#   LUME_VERSION 默认 v0.11.0, LUME_REPO 默认 erishen/lume
#
# 幂等 + 防版本漂移(踩过的坑): 只比「本地 tgz 与本地 .sha 是否自洽」是不够的 ——
# 换 LUME_VERSION 时旧的 tgz/.sha 是一起下载的, 自洽但版本是旧的(曾把 0.6.2 当 0.11.0
# 打进镜像)。所以必须拿**上游官方 digest** 比对:
#   - 优先 GitHub API 的 asset.digest(release 资源接口, 走 api.github.com, 比
#     release 下载域更稳);
#   - 兜底用 release 里的 .sha256 文件;
#   - 两者都取不到 → 不能证明版本正确, 不写版本戳, 让 docker-fetch 失败而不是静默放过。
set -e

VERSION="${1:-v0.11.0}"
REPO="${2:-erishen/lume}"
cd "$(dirname "$0")/.."   # 回仓库根, vendor/ 相对根

# 本机 arch → 资产 arch 名
a_case=$(uname -m)
case "$a_case" in
  arm64|aarch64) a=arm64 ;;
  x86_64|amd64)  a=x64   ;;
  *) echo "unsupported arch: $a_case" >&2; exit 1 ;;
esac

ASSET="lume-linux-${a}-static.tar.gz"
tgz="vendor/${ASSET}"
sha="vendor/lume-linux-${a}-static.sha"
vfile="vendor/.lume-version"
dl="https://github.com/${REPO}/releases/download/${VERSION}"

# 统一 sha256(macOS shasum / Linux sha256sum)
hash() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

# python3: urllib 会读系统/环境代理配置, 对 GitHub release 域名的连通性通常好于直连 curl
# (本机实测 curl 直连 release 下载域会超时挂住, urlopen 正常)。取不到就退回 curl。
PY3=""
command -v python3 >/dev/null 2>&1 && PY3=python3

# 上游官方 sha256(仅 hex)。取不到 → 空串。进度输出走 stderr, 保证 stdout 干净。
fetch_official_sha() {
  # 路径 A: GitHub API 的 asset.digest
  if [ -n "$PY3" ]; then
    d=$("$PY3" - "$REPO" "$VERSION" "$ASSET" <<'PY' 2>/dev/null || true
import json, sys, urllib.request
repo, tag, asset = sys.argv[1:4]
url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
try:
    with urllib.request.urlopen(url, timeout=25) as r:
        rel = json.load(r)
except Exception:
    sys.exit(0)
for a in rel.get("assets", []):
    if a.get("name") == asset:
        dg = str(a.get("digest") or "")
        if dg.startswith("sha256:"):
            print(dg.split(":", 1)[1])
        break
PY
)
    d=$(printf '%s' "$d" | tr -d '[:space:]')
    if [ -n "$d" ]; then printf '%s\n' "$d"; return 0; fi
  fi
  # 路径 B: release 里的 .sha256 文件(首行首列)
  tmp_sha="${TMPDIR:-/tmp}/lume-$VERSION-${a}.sha256"
  if curl -fsSL --retry 3 --retry-delay 2 --max-time 30 -o "$tmp_sha" \
        "${dl}/lume-linux-${a}-static.sha256" 2>/dev/null; then
    awk 'NR==1{print $1}' "$tmp_sha"
    return 0
  fi
  return 1
}

mkdir -p vendor
echo "▶ 目标 ${VERSION} / ${a}, 解析上游官方 sha256 …" >&2
official=$(fetch_official_sha || true)
official=$(printf '%s' "$official" | tr -d '[:space:]')
if [ -n "$official" ]; then
  echo "  官方 sha256 = ${official}" >&2
else
  echo "  ⚠ 取不到上游 sha256(网络不通?), 无法证明 vendor 版本, 该轮不写版本戳" >&2
fi

# ---- 1. 本地已有包: 必须同时满足「版本戳一致」+「与官方 digest 一致」 ----
if [ -f "$tgz" ]; then
  stamp=""
  [ -f "$vfile" ] && stamp=$(head -n1 "$vfile" | tr -d '[:space:]')
  act=$(hash "$tgz")
  if [ -n "$official" ] && [ "$act" = "$official" ] && [ "$stamp" = "$VERSION" ]; then
    echo "✓ ${tgz} 已是 ${VERSION} 且与官方 digest 一致, 跳过下载" >&2
    printf '%s\n' "$VERSION" > "$vfile"
    exit 0
  fi
  if [ -n "$official" ] && [ "$act" != "$official" ]; then
    echo "⚠ 本地包 sha=${act:0:12}… ≠ 官方 ${official:0:12}…(旧版本/被改动), 重新下载" >&2
  elif [ "$stamp" != "$VERSION" ]; then
    echo "⚠ 版本戳 ${stamp:-缺失} ≠ ${VERSION}, 重新下载" >&2
  fi
  rm -f "$tgz" "$sha" "$vfile"
fi

# ---- 2. 下载 ----
# 优先 python3(urllib 走系统代理设置, GitHub release 域名常被直连 curl 挂住), 兜底 curl。
echo "▶ 宿主下载 ${VERSION} 的 ${ASSET} …" >&2
if [ -n "$PY3" ]; then
  "$PY3" - "$dl/$ASSET" "${tgz}.part" <<'PY' || { rm -f "${tgz}.part"; echo "✗ 下载失败(${ASSET})" >&2; exit 1; }
import sys, urllib.request
url, dst = sys.argv[1], sys.argv[2]
with urllib.request.urlopen(url, timeout=60) as r, open(dst, "wb") as f:
    while True:
        b = r.read(1 << 16)
        if not b:
            break
        f.write(b)
PY
else
  curl -fL --retry 5 --retry-delay 2 --max-time 600 -o "${tgz}.part" "${dl}/${ASSET}"
fi
act=$(hash "${tgz}.part")

# ---- 3. 校验: 官方 digest > release .sha256 > 拒绝 ----
if [ -n "$official" ]; then
  if [ "$act" != "$official" ]; then
    rm -f "${tgz}.part"
    echo "✗ sha256 不匹配 (官方=${official} 实际=${act}), 丢弃" >&2
    exit 1
  fi
  echo "✓ 与官方 digest 一致" >&2
else
  # 官方取不到时退一步用 release 的 .sha256; 仍然拿不到就失败(避免又一个「自洽的旧包」)
  if curl -fsSL --retry 3 --retry-delay 2 --max-time 30 -o "$sha" \
        "${dl}/lume-linux-${a}-static.sha256"; then
    exp=$(awk 'NR==1{print $1}' "$sha")
    if [ -n "$exp" ] && [ "$exp" != "$act" ]; then
      rm -f "${tgz}.part"
      echo "✗ sha256 不匹配 (expected=${exp} actual=${act})" >&2
      exit 1
    fi
    official=$exp
    echo "✓ 与 release .sha256 一致" >&2
  else
    rm -f "${tgz}.part"
    echo "✗ 取不到任何官方 sha256, 无法确认下载包版本; 检查网络后重试" >&2
    exit 1
  fi
fi

mv "${tgz}.part" "$tgz"
printf '%s  %s\n' "$official" "$ASSET" > "$sha"
printf '%s\n' "$VERSION" > "$vfile"
echo "✓ ${tgz} ready ($(ls -la "$tgz" | awk '{print $5}') bytes, ${VERSION})"
