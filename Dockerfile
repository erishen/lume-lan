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

ARG LUME_VERSION=v0.12.1
ARG LUME_REPO=erishen/lume
# ══ 为什么是 alpine 而不是 python:3.12-slim ═════════════════════════════
# 之前用 python:3.12-slim, 镜像 168MB, 其中 101MB 是基础镜像本身。
# 而这个镜像里 Python 的唯一用途是 entrypoint.sh 里那一行
#   python3 scripts/router_fetch.py        (REFRESH=1 时的刷新循环)
# 该脚本及其依赖(webhook_notify.py) **只用标准库**:
#   base64 hashlib hmac json os re sys time urllib.*
# 所以真正需要的是"一个能跑标准库的 python3", 不是 CPython 官方镜像
# 那一整套(gcc/头文件/pip/setuptools/site-packages)。
#
# alpine 3.20 的 python3 包自带完整标准库, 实测两个脚本都能解析运行。
# 实测体积: python:3.12-slim 168MB → alpine 59MB。
#
# 保留 curl: lume 的 /react/api/chat 用 execlp("curl") 调模型(TLS 交给
# curl), 没有它这条链路直接失败(实测报 curl exit 7)。
FROM alpine:3.20 AS base

ARG TARGETARCH
ARG LUME_VERSION
ARG LUME_REPO

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # 默认只读 .data/ 快照起看板; 需要容器内自刷新设 REFRESH=1
    REFRESH=0 \
    REFRESH_SECS=60 \
    # 路由器后台地址(Mac Docker Desktop 走默认 NAT 通常直达局域网; Linux 建议 network_mode: host)
    # 留空 = 由 router_fetch.py 从 .data/devices.json 的 gateway 推导, 不写死网段。
    ROUTER_HOST=

# ══ 为什么不再装 python3 ═══════════════════════════════════════════════
# 之前装 python3 只为一个用途: REFRESH=1 时的刷新循环跑 router_fetch.py。
# 实测 python3 占 44MB, 是整个镜像 76MB 的 58%。
#
# 现在那个循环跑的是 scripts/router_fetch.run.lume —— 同样的登录流程
# (CSRF → SHA256 登录 → 带 cookie 取 HostInfo/topology/wan), 纯 lume。
# 前提是 http_* 能读到响应头(Set-Cookie 才能维持会话), 见 builtins_http.c。
#
# 保留 curl: lume 的 /react/api/chat 用 execlp("curl") 调模型(TLS 交给
# curl), 没有它这条链路直接失败(实测报 curl exit 7)。
RUN apk add --no-cache curl ca-certificates tzdata

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
    # TARGETARCH 由 buildx 提供; legacy builder 不设 → 自动探测 vendor/ 里的实际 tarball
    arch="${TARGETARCH:-}"; \
    if [ -z "${arch}" ]; then \
      if [ -f "/vendor-src/lume-linux-arm64-static.tar.gz" ]; then arch=arm64; \
      elif [ -f "/vendor-src/lume-linux-x64-static.tar.gz" ]; then arch=x64; fi; \
    fi; \
    case "${arch}" in \
      amd64)  arch=x64   ;; \
      arm64)  arch=arm64 ;; \
      *) echo "cannot determine target arch (TARGETARCH='${TARGETARCH}', no matching vendor tarball)" >&2; ls -la /vendor-src >&2; exit 1 ;; \
    esac; \
    install_lume() { \
      mkdir -p /opt/lume; \
      tar -xzf /tmp/lume.tar.gz -C /opt/lume; \
      bin=$(find /opt/lume -type f -name 'lume' ! -name '*.tmp' | head -1); \
      [ -n "${bin}" ] || { echo "lume binary not found in tarball" >&2; ls -R /opt/lume >&2; exit 1; }; \
      install -m 0755 "${bin}" /usr/local/bin/lume; \
      rm -rf /opt/lume; \
    }; \
    # vendor/ 由 make docker-fetch 生成, 带 .lume-version 版本戳; 与本次 build 指定的
    # LUME_VERSION 不一致就直接失败 —— 否则会静默用旧二进制打镜像(版本漂移)。
    if [ -f /vendor-src/.lume-version ]; then \
      stamp=$(head -n1 /vendor-src/.lume-version | tr -d '[:space:]'); \
      if [ "${stamp}" != "${LUME_VERSION}" ]; then \
        echo "vendor/ 是 ${stamp}, 但 LUME_VERSION=${LUME_VERSION} → 先跑 make docker-fetch LUME_VERSION=${LUME_VERSION}" >&2; \
        exit 1; \
      fi; \
    fi; \
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
# 只带容器里真正会跑的脚本。
# scripts/ 下还有 13 个 .py(scan_lan/adguard_fetch/dns_watch…), 它们要么
# 依赖宿主网卡、要么是宿主的 make 目标 —— 容器里既没有 python3 也不会调用。
# 整目录 COPY 会把它们(含 3.1MB __pycache__)一起装进镜像, 看起来像还能用,
# 实际是死代码; 以后有人照着改, 会发现改了不生效。
COPY scripts/router_fetch.lume ./scripts/
COPY scripts/router_fetch.run.lume ./scripts/
COPY www/ ./www/
# lume-core/lib: peer / identity 等共享模块。lan.lume 与 peer_node.lume 都 import
# "../lume-core/lib/peer.lume", 从 /app 看就是 /lume-core/lib/peer.lume —— 所以
# 必须在镜像里放在**根目录**下, 且与宿主布局同名, 否则 import 解析失败。
# (构建上下文里由 make docker-build 先把 ../lume-core/lib 同步到 .runtime/, 见
#  下方 build 前的说明; 直接 COPY ../ 是不允许的。)
# ── lume-core/lib: 按版本从 release 取 ─────────────────────────────────
# 这 10 个 .lume 模块是源码(运行时 import 读盘), 不编进二进制。
# 以前从宿主 ../lume-core/lib 复制进 .runtime/, 那份副本没有版本标记,
# 会静默停在旧版 —— 症状是 "module 'peer' has no export '...'" 这种
# 指向调用点、指不到旧文件的报错。改为 pin 版本从发布包取(v0.12.1 起带 lib/)。
ARG LUME_CORE_VERSION=v0.12.1
ARG LUME_CORE_REPO=erishen/lume-core
RUN set -eux; \
    case "${TARGETARCH:-arm64}" in \
      amd64) carch=x64 ;; \
      arm64) carch=arm64 ;; \
      *) echo "unsupported TARGETARCH='${TARGETARCH}'" >&2; exit 1 ;; \
    esac; \
    base="https://github.com/${LUME_CORE_REPO}/releases/download/${LUME_CORE_VERSION}"; \
    asset="lume-core-linux-${carch}"; \
    curl -fsSL --retry 3 --retry-delay 2 -o /tmp/core.tar.gz "${base}/${asset}.tar.gz"; \
    curl -fsSL --retry 3 -o /tmp/core.sha256 "${base}/${asset}.sha256"; \
    (cd /tmp && sed "s|${asset}.tar.gz|core.tar.gz|" core.sha256 | sha256sum -c -); \
    mkdir -p /tmp/core && tar -xzf /tmp/core.tar.gz -C /tmp/core; \
    mkdir -p /lume-core; \
    cp -r "/tmp/core/${asset}/lib" /lume-core/lib; \
    test -f /lume-core/lib/peer.lume; \
    rm -rf /tmp/core /tmp/core.tar.gz /tmp/core.sha256
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# .data/ 快照(宿主 make scan/router-fetch 产出, 或容器内循环写); .env 路由器凭据
VOLUME ["/app/.data"]

EXPOSE 8091

# 健康检查: Lume 起来后根路径应可响应。Basic Auth 开启后匿名访问为 401(认证层正常,
# 也算服务存活), 故不用 -f(否则 -f 会把 401 当失败标记 unhealthy)。curl 已在镜像内。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sS -o /dev/null http://127.0.0.1:8091/ || exit 1

ENTRYPOINT ["/entrypoint.sh"]
