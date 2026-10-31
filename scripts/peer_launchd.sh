#!/usr/bin/env sh
# 用 launchd 守护各应用的 peer 节点与 agent, 让它们登录自启、崩溃自动重启。
#
# ── 为什么需要 ──────────────────────────────────────────────────
# 这些节点之前是 `nohup ... &` 起的普通后台进程, 没有任何守护:
#   * 终端关掉 / 系统休眠唤醒后可能消失;
#   * 进程被外部杀掉后不会自己回来;
#   * 而看板只会显示"不可达", 看不出是"崩了"还是"从没起过"。
# 实测发生过一次: blog/crm/scanner 三个节点同时不在, 但日志里没有任何
# 崩溃痕迹(是被外部终止的) —— 没有守护就只能人工重开。
#
# ── 用法 ────────────────────────────────────────────────────────
#   sh scripts/peer_launchd.sh install   # 安装并立即加载
#   sh scripts/peer_launchd.sh status    # 看哪些在跑
#   sh scripts/peer_launchd.sh uninstall # 卸载
#
# 安装后 KeepAlive 会保证进程退出即重启; RunAtLoad 负责登录自启。
set -e

LUME_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"   # → work/lume/
LA_DIR="$HOME/Library/LaunchAgents"
LUME_BIN="$LUME_ROOT/lume/bin/lume"
AGENT_BIN="$LUME_ROOT/lume/agent-httpd/bin/agent-httpd"

# 节点清单: 标签后缀 | 目录 | 节点名 | 端口 | 是否需要 agent | agent 端口
NODES="
blog:lume-blog:blog:18321:0:0
crm:lume-crm:crm:18102:0:0
scanner:lume-scanner:scanner:18103:1:18199
lan:lume-lan:lan:18092:0:0
"

plist_peer() {
    # $1=label $2=dir $3=node $4=port
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.erishen.lume-peer-$1</string>
  <key>ProcessType</key><string>Background</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-c</string>
    <string>cd "$2" &amp;&amp; exec env PEER_NAME=$3 PEER_PORT=$4 PEER_BIND=127.0.0.1 PEER_SHARED="\$(grep -E '^PEER_SHARED=' .env | cut -d= -f2-)" PEER_ROOT="\$(grep -E '^PEER_ROOT=' .env | cut -d= -f2-)" PEER_SITE_NAME=$2 $LUME_BIN peer_node.lume</string>
  </array>
  <key>RunAtLoad</key><true/>
  <!-- KeepAlive: 退出即重启。这是与 nohup 的本质区别 —— 后者死了就死了。 -->
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$2/.runtime-peer.log</string>
  <key>StandardErrorPath</key><string>$2/.runtime-peer.log</string>
</dict>
</plist>
EOF
}

plist_agent() {
    # $1=label $2=dir $3=port
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.erishen.lume-agent-$1</string>
  <key>ProcessType</key><string>Background</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-c</string>
    <string>cd "$2" &amp;&amp; exec env LLM_API_URL="\$(grep -E '^LLM_API_URL=' .env | cut -d= -f2-)" LLM_MODEL="\$(grep -E '^LLM_MODEL=' .env | cut -d= -f2-)" LLM_API_KEY="\$(grep -E '^LLM_API_KEY=' .env | cut -d= -f2-)" $AGENT_BIN -p $3 -w 2</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$2/.runtime-agent.log</string>
  <key>StandardErrorPath</key><string>$2/.runtime-agent.log</string>
</dict>
</plist>
EOF
}

do_install() {
    mkdir -p "$LA_DIR"
    echo "$NODES" | while IFS=: read -r label app node port has_agent aport; do
        [ -z "$label" ] && continue
        dir="$LUME_ROOT/$app"
        [ -d "$dir" ] || { echo "  跳过 $label（缺目录 $dir）"; continue; }

        f="$LA_DIR/com.erishen.lume-peer-$label.plist"
        plist_peer "$label" "$dir" "$node" "$port" > "$f"
        launchctl unload "$f" 2>/dev/null || true
        launchctl load "$f"
        echo "  ✓ peer $node :$port  → $f"

        if [ "$has_agent" = "1" ]; then
            f2="$LA_DIR/com.erishen.lume-agent-$label.plist"
            plist_agent "$label" "$dir" "$aport" > "$f2"
            launchctl unload "$f2" 2>/dev/null || true
            launchctl load "$f2"
            echo "  ✓ agent $node :$aport → $f2"
        fi
    done
    echo
    echo "已安装。查看状态: sh scripts/peer_launchd.sh status"
}

do_uninstall() {
    for f in "$LA_DIR"/com.erishen.lume-peer-*.plist "$LA_DIR"/com.erishen.lume-agent-*.plist; do
        [ -f "$f" ] || continue
        launchctl unload "$f" 2>/dev/null || true
        rm -f "$f"
        echo "  ✓ 已卸载 $(basename "$f")"
    done
}

do_status() {
    echo "$NODES" | while IFS=: read -r label app node port has_agent aport; do
        [ -z "$label" ] && continue
        s=$(launchctl list | awk -v L="com.erishen.lume-peer-$label" '$3==L{print $1}')
        n=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | wc -l | tr -d ' ')
        printf "  %-8s :%-6s launchd=%-8s 监听=%s\n" "$node" "$port" "${s:-未加载}" "$([ "$n" -gt 0 ] && echo 是 || echo 否)"
        if [ "$has_agent" = "1" ]; then
            s2=$(launchctl list | awk -v L="com.erishen.lume-agent-$label" '$3==L{print $1}')
            n2=$(lsof -nP -iTCP:"$aport" -sTCP:LISTEN 2>/dev/null | wc -l | tr -d ' ')
            printf "  %-8s :%-6s launchd=%-8s 监听=%s\n" "$node-agent" "$aport" "${s2:-未加载}" "$([ "$n2" -gt 0 ] && echo 是 || echo 否)"
        fi
    done
}

case "${1:-}" in
    install)   do_install ;;
    uninstall) do_uninstall ;;
    status)    do_status ;;
    *) echo "用法: $0 install|status|uninstall" >&2; exit 1 ;;
esac
