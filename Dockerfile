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

# ---- 取 Lume 全静态 linux 二进制装到 /usr/local/bin/lume ----
# 首选「宿主预下载」: make docker-fetch 在本机按 arch 把 static tarball 下到 vendor/
# (宿主网络通), build 时 COPY 进镜像并离线安装 —— 绕开 OrbStack 容器内 curl
# 走网关 TLS 被截断(unexpected eof / exit 35)。
# 若 vendor/ 里没有匹配 arch 的文件(例如从零 buildx 多架构 / CI), 回落到容器内 curl。
# 资产命名(erishen/lume release.yml 权威):
#   静态 tarball = lume-linux-<x64|arm64>-static.tar.gz   (解压后: <asset>-static/bin/lume)
#   校验文件     = lume-linux-<x64|arm64>-static.sha      (sha256sum 生成, 首行首列=hex)
# vendor/ 总会被 COPY(缺 .gitkeep 兜底目录); RUN 里按 TARGETARCH 挑文件。
COPY vendor/ /vendor-src/

RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64)  arch=x64   ;; \
      arm64)  arch=arm64 ;; \
      *) echo "unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    install_lume() { \
      mkdir -p /opt/lume; \
      tar -xzf /tmp/lume.tar.gz -C /opt/lume; \
      bin=$(find /opt/lume -type f -name 'lume' ! -name '*.tmp' | head -1); \
      [ -n "${bin}" ] || { echo "lume binary not found in tarball" >&2; ls -R /opt/lume >&2; exit 1; }; \
      install -m 0755 "${bin}" /usr/local/bin/lume; \
      rm -rf /opt/lume; \
    }; \
    src="/vendor-src/lume-linux-${arch}-static.tar.gz"; \
    sha="/vendor-src/lume-linux-${arch}-static.sha"; \
    if [ -f "${src}" ]; then \
      echo "==> using host-provisioned ${src} (offline, skip in-container download)"; \
      cp "${src}" /tmp/lume.tar.gz; \
      if [ -f "${sha}" ]; then \
        hex=$(awk 'NR==1{print $1}' "${sha}"); \
        echo "${hex}  /tmp/lume.tar.gz" | sha256sum -c -; \
      fi; \
      install_lume; \
    else \
      echo "==> no host-provisioned ${arch} tarball in vendor/; downloading in-container (with retries)"; \
      dl="https://github.com/${LUME_REPO}/releases/download/${LUME_VERSION}"; \
      asset="lume-linux-${arch}-static"; \
      ok=0; \
      for i in 1 2 3 4 5; do \
        if curl -fsSL --retry 3 --retry-delay 2 -o /tmp/lume.tar.gz "${dl}/${asset}.tar.gz"; then ok=1; break; fi; \
        echo "download attempt ${i} failed, retrying…" >&2; sleep 5; \
      done; \
      [ "${ok}" = "1" ] || { echo "in-container download failed after 5 tries (run host 'make docker-fetch' + retry build)" >&2; exit 1; }; \
      if curl -fsSL --retry 3 -o /tmp/lume.sha "${dl}/${asset}.sha256" 2>/dev/null; then \
        hex=$(awk 'NR==1{print $1}' /tmp/lume.sha); \
        echo "${hex}  /tmp/lume.tar.gz" | sha256sum -c - || echo "(sha 校验失败, 仍继续安装)"; \
      fi; \
      install_lume; \
    fi; \
    rm -rf /vendor-src /tmp/lume.tar.gz /tmp/lume.sha; \
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

# 健康检查: Lume 起来后根路径(或 /api/meta)应 200。docker compose / docker 能据此
# 标记 unhealthy(容器挂了/端口没起), 编排器可自动重启。curl 已在镜像内。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS -o /dev/null http://127.0.0.1:8091/ || exit 1

ENTRYPOINT ["/entrypoint.sh"]
