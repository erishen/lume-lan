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
# ---- Basic Auth: 由 KANBAN_AUTH 决定, 默认关 ----
#
# 为什么需要在容器里判断, 而不是 compose 里用 ${VAR:+...}:
# compose 没法"有条件地不定义某个环境变量" —— 空值时会留下一个值为 "" 的键,
# 而 lume 把"变量存在"就当作启用了认证, 于是去 open("") 失败, 日志刷
# "cannot open htpasswd file: "(后面是空的), 容器被 restart 策略反复重启。
#
# 所以这里显式 unset: 关闭时变量真的不存在, lume 走"无认证"分支。
if [ -n "${KANBAN_AUTH:-}" ]; then
  if [ ! -f "${HTPASSWD_FILE:-}" ]; then
    echo "✗ KANBAN_AUTH 已开但找不到 ${HTPASSWD_FILE:-/auth/htpasswd}" >&2
    echo "  先 make auth-pass 生成 .runtime/htpasswd" >&2
    exit 1
  fi
  echo "auth: Basic Auth via $HTPASSWD_FILE"
else
  # 默认关闭: 容器只发布到 127.0.0.1, 外部连不进来, 认证收益小,
  # 而 Basic 的凭据缓存不可靠(每次刷新都要重登)。
  unset HTPASSWD_FILE
  echo "auth: disabled (本机部署默认; 要开启: KANBAN_AUTH=1)"
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
  echo "refresh loop pid $! (ROUTER_HOST=${ROUTER_HOST:-自动取 .data/devices.json 的 gateway})"
fi

# 前台起 Lume; 容器停止时随之退出
exec lume lan.lume
