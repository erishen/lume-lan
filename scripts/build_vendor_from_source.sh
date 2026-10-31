# 用**当前源码**重编厂商静态 Linux 版 lume, 产物直接落进 lume-lan/vendor/。
#
# 为什么需要它: 上游 release 的二进制不带本地修复。实测踩过 —— 容器里
# 跑的是 2026-10-28 release 产物, 而 fork 前 TLS 预热(lume_http_warmup)
# 是之后才加的, 于是容器里所有 A2A 出站(审批/调用工具/提问)在首次 HTTPS
# 时都会撞 macOS→Linux 之外的另一种表现, 而源码看着是好的。
#
# 与 vendor/ 常规来源的区别:
#   make docker-fetch  → 从 GitHub release 下, 有上游 digest 可校验
#   make vendor-src    → 从 ../lume 的**工作区源码**编, 无法与上游校验
# 所以本脚本额外写 vendor/.lume-build 记录来源与 commit, 保证可追溯;
# .lume-version 仍保持基础版本号(v0.11.0), 否则 Dockerfile 的版本闸门会拒绝。
#
# 用法: cd lume-lan && make vendor-src
set -e

cd "$(dirname "$0")/.."      # → lume-lan/
LUME_SRC="${LUME_SRC:-../lume}"

[ -f "$LUME_SRC/Makefile" ] || {
    echo "找不到 lume 源码树: $LUME_SRC" >&2
    echo "(用 LUME_SRC=/path/to/lume 指定)" >&2
    exit 1
}

case "$(uname -m)" in
    arm64|aarch64) ARCH=arm64 ;;
    x86_64|amd64)  ARCH=x64 ;;
    *) echo "unsupported arch: $(uname -m)" >&2; exit 1 ;;
esac

TAG="lume-vendorbuild:${ARCH}"
OUT=".runtime/vendorout"
mkdir -p "$OUT"

echo "▶ 用源码编译静态 lume ($ARCH)"
echo "  源码: $LUME_SRC"
echo "  HEAD: $(cd "$LUME_SRC" && git rev-parse --short HEAD 2>/dev/null || echo unknown)"

docker build \
    -f scripts/Dockerfile.vendorbuild \
    --build-arg VENDOR_ARCH="$ARCH" \
    -t "$TAG" \
    "$LUME_SRC"

docker run --rm -v "$PWD/$OUT:/export" "$TAG"

TGZ="$OUT/pkg.tgz"
[ -f "$TGZ" ] || { echo "构建没有产出 $TGZ" >&2; exit 1; }

# 自检: 修复必须真的在这个二进制里, 否则打进去的是个"看起来新"的旧版。
# 注意 grep -q 会提前关管道, tar 于是报 "Broken pipe" —— 那不是失败。
# 先落到临时文件再查, 避免把 SIGPIPE 的噪声混进输出, 让人误判构建失败。
CHECK="$(mktemp)"
tar -xzf "$TGZ" -O "lume-linux-${ARCH}-static/lume" > "$CHECK"
if ! strings "$CHECK" | grep -q "TLS warmup"; then
    rm -f "$CHECK"
    echo "✗ 产物里没有 lume_http_warmup 的痕迹 —— 源码是否已包含该修复?" >&2
    exit 1
fi
rm -f "$CHECK"
echo "  ✓ 产物含 TLS 预热修复"

install -m 0644 "$TGZ" "vendor/lume-linux-${ARCH}-static.tar.gz"
if command -v sha256sum >/dev/null 2>&1; then
    hex=$(sha256sum "$TGZ" | cut -d' ' -f1)
else
    hex=$(shasum -a 256 "$TGZ" | cut -d' ' -f1)
fi
printf '%s  lume-linux-%s-static.tar.gz\n' "$hex" "$ARCH" \
    > "vendor/lume-linux-${ARCH}-static.sha"

# 基础版本号保持不变: Dockerfile 拿它跟 LUME_VERSION 比对, 改成别的会被拒。
printf 'v0.11.0' > vendor/.lume-version

cat > vendor/.lume-build <<EOF
source=local-source-build
base_version=v0.11.0
built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
platform=linux/${ARCH}
git_lume=$(cd "$LUME_SRC" && git rev-parse --short HEAD 2>/dev/null || echo unknown)
git_agenthttpd=$(cd "$LUME_SRC/agent-httpd" && git rev-parse --short HEAD 2>/dev/null || echo unknown)
uncommitted=$(cd "$LUME_SRC" && git status --porcelain | wc -l | tr -d ' ')
note=含 fork 前 TLS 预热修复 (lume_http_warmup), 非上游 release 产物
EOF

echo "✓ 已更新 vendor/lume-linux-${ARCH}-static.tar.gz"
echo "  sha256: $hex"
echo "  未提交改动数: $(cd "$LUME_SRC" && git status --porcelain | wc -l | tr -d ' ')"
echo
echo "下一步: make docker-build && docker compose up -d"
