#!/usr/bin/env sh
# 宿主预下载 Lume 全静态 linux 二进制到 vendor/(供 Docker build 离线 COPY, 绕开 OrbStack
# 容器内 curl 走网关 TLS 被截断 exit 35)。按本机 arch 取对应 static 资产, 幂等:
# vendor/ 已有同 arch 且校验通过则跳过。
#
# 用法: scripts/fetch_lume_vendor.sh [LUME_VERSION] [LUME_REPO]
#   LUME_VERSION 默认 v0.6.3, LUME_REPO 默认 erishen/lume
set -e

VERSION="${1:-v0.6.3}"
REPO="${2:-erishen/lume}"
cd "$(dirname "$0")/.."   # 回仓库根, vendor/ 相对根

# 本机 arch → 资产 arch 名
a_case=$(uname -m)
case "$a_case" in
  arm64|aarch64) a=arm64 ;;
  x86_64|amd64)  a=x64   ;;
  *) echo "unsupported arch: $a_case" >&2; exit 1 ;;
esac

tgz="vendor/lume-linux-${a}-static.tar.gz"
sha="vendor/lume-linux-${a}-static.sha"
dl="https://github.com/${REPO}/releases/download/${VERSION}"

# 统一 sha256(macOS shasum / Linux sha256sum)
hash() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

mkdir -p vendor

# 幂等: 已存在且校验和匹配则跳过
if [ -f "$tgz" ] && [ -f "$sha" ]; then
  exp=$(awk 'NR==1{print $1}' "$sha")
  act=$(hash "$tgz")
  if [ -n "$exp" ] && [ "$exp" = "$act" ]; then
    echo "✓ ${tgz} 已存在且校验通过, 跳过下载"
    exit 0
  fi
  echo "⚠ 已存在但校验和变化, 重新下载…"
fi

echo "▶ 宿主下载 ${VERSION} 的 lume-linux-${a}-static → ${tgz} …"
curl -fL --retry 5 --retry-delay 2 -o "$tgz" "${dl}/lume-linux-${a}-static.tar.gz"
curl -fL -o "$sha" "${dl}/lume-linux-${a}-static.sha256" || true

if [ -s "$sha" ]; then
  exp=$(awk 'NR==1{print $1}' "$sha")
  act=$(hash "$tgz")
  if [ -n "$exp" ] && [ "$exp" != "$act" ]; then
    echo "✗ sha256 不匹配 (expected=${exp} actual=${act})" >&2
    exit 1
  fi
  echo "✓ 校验通过"
else
  echo "⚠ 未取到 .sha256(可能 404), 跳过校验(仍已下载 tarball)"
fi
echo "✓ ${tgz} ready ($(ls -la "$tgz" | awk '{print $5}') bytes)"
