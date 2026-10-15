#!/bin/sh
# Lume-LAN 容器入口
#   前台: 起 lume server(serve /app/www + 读 /app/.data 快照, 绑 0.0.0.0:8091)
#   可选: REFRESH=1 时后台循环跑 router_fetch.py(容器内打路由器, 更新 .data/)
#   arp 快照需宿主网卡, 容器内不做 —— 宿主的 make scan 写进挂载的 .data/
set -e
cd /app
mkdir -p .data .runtime

# Lume 默认绑 127.0.0.1; 容器内开放到 0.0.0.0, 让 docker -p 能映射进来
export LUME_BIND="${LUME_BIND:-0.0.0.0}"
# 可选 Basic Auth: 注入 HTPASSWD_FILE 才开(公网部署才需要, 容器内用可省)
if [ -n "${HTPASSWD_FILE:-}" ]; then
  echo "auth: Basic Auth via $HTPASSWD_FILE"
fi

LUME_VER="$(lume --version 2>/dev/null | head -1 || echo lume)"
echo "serving ${LUME_VER} on 0.0.0.0:8091 (docroot=/app/www, data=/app/.data)"

# 容器内自刷新: 打 ROUTER_HOST 更新 .data/router.json(路由器流量历史/终端/活跃)
# arp 扫描(scan_lan.py)依赖宿主网卡, 不在容器里做 —— 宿主的 make scan 负责
if [ "${REFRESH}" = "1" ]; then
  (
    echo "refresh loop: 每 ${REFRESH_SECS:-60}s 跑 router_fetch.py(容器内, 打路由器)"
    while true; do
      python3 scripts/router_fetch.py >/dev/null 2>&1 || true
      sleep "${REFRESH_SECS:-60}"
    done
  ) &
  echo "refresh loop pid $! (ROUTER_HOST=${ROUTER_HOST:-192.168.3.1})"
fi

# 前台起 Lume; 容器停止时随之退出
exec lume lan.lume
