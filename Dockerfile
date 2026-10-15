# Lume LAN — 看板容器
#
# 跑产品版 `lume`(带 server/run 的发行版, 从 GitHub 预编译 static linux 二进制拉取,
# 免装 libLLVM/LLVM)。前端 www/ 是仓库里 build 好的 bundle(直接 COPY, 不跑 node/esbuild)。
# 数据刷新(打路由器 / arp)由宿主 make dev 做, 把 .data/ 挂载进来; 或容器内 REFRESH=1
# 起后台 router_fetch 循环(打 ROUTER_HOST)。
#
# 多架构: x86_64 与 arm64(Apple Silicon 的 Docker Desktop 默认 arm64 容器)。
#   docker buildx build --platform linux/arm64,linux/amd64 -t lume-lan .
#   或本地单架构: docker build -t lume-lan . (自动按本机 arch 取对应二进制)

ARG LUME_VERSION=v0.6.3
ARG LUME_REPO=erishen/lume
FROM python:3.12-slim AS base

ARG TARGETARCH
ARG LUME_VERSION
ARG LUME_REPO

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # 默认只读 .data/ 快照起看板; 需要容器内自刷新设 REFRESH=1
    REFRESH=0 \
    REFRESH_SECS=60 \
    # 路由器后台地址(Mac Docker Desktop 走默认 NAT 通常直达局域网; Linux 建议 network_mode: host)
    ROUTER_HOST=192.168.3.1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 拉取并校验对应架构的 static linux 二进制, 装到 /usr/local/bin/lume。
# 资产命名(erishen/lume release.yml 权威):
#   静态 tarball = lume-linux-<x64|arm64>-static.tar.gz   (解压后: <asset>-static/bin/lume)
#   校验文件     = lume-linux-<x64|arm64>-static.sha256   (sha256sum 生成, 首行首列=hex)
# 完全静态(无 glibc/libsqlite 运行时依赖), 放进 slim 也稳; 故用 slim 而非 scratch。
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64)  arch=x64   ;; \
      arm64)  arch=arm64 ;; \
      *) echo "unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    dl="https://github.com/${LUME_REPO}/releases/download/${LUME_VERSION}"; \
    asset="lume-linux-${arch}-static"; \
    curl -fsSL -o /tmp/lume.tar.gz "${dl}/${asset}.tar.gz"; \
    curl -fsSL -o /tmp/lume.sha    "${dl}/${asset}.sha256"; \
    hex=$(awk 'NR==1{print $1}' /tmp/lume.sha); \
    echo "${hex}  /tmp/lume.tar.gz" | sha256sum -c -; \
    mkdir -p /opt/lume; \
    tar -xzf /tmp/lume.tar.gz -C /opt/lume; \
    bin=$(find /opt/lume -type f -name 'lume' ! -name '*.tmp' | head -1); \
    [ -n "${bin}" ] || { echo "lume binary not found in ${asset}.tar.gz" >&2; ls -R /opt/lume >&2; exit 1; }; \
    install -m 0755 "${bin}" /usr/local/bin/lume; \
    rm -rf /opt/lume /tmp/lume.tar.gz /tmp/lume.sha; \
    /usr/local/bin/lume --version

WORKDIR /app
# 布局必须与 lan.lume / devices.lume 的相对路径一致(容器 CWD=/app):
#   docroot=./www            → /app/www   (前端 bundle, Lume 惰性读盘)
#   import "src/devices.lume" → /app/src   (保留 src/ 前缀! 平铺会断链)
#   .data/*.json            → /app/.data (宿主 make scan/router-fetch 挂载进来)
COPY lan.lume ./
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY www/ ./www/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# .data/ 快照(宿主 make scan/router-fetch 产出, 或容器内循环写); .env 路由器凭据
VOLUME ["/app/.data"]

EXPOSE 8091

ENTRYPOINT ["/entrypoint.sh"]
