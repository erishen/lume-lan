# lume-lan Makefile — 局域网设备看板(Lume 服务端 + React + 宿主扫描脚本)
#
# 常用:
#   make dev       日常入口:先停掉 PORT 上的旧服务/残留 watch,再 check+ui+scan+起服务
#                  (前台阻塞,Ctrl-C 退出;重复执行会先清掉上次的实例,不会撞端口)
#   make stop      只停:kill PORT 上旧服务 + 残留 esbuild watch(不重启)
#   make lan       make dev 的别名(向后兼容)
#   make lan-dev   check + 前端 --watch + 起服务(改 .tsx 即刷,改 .lume 重启生效)
#
# 分步:
#   make scan        跑宿主扫描脚本(arp 现成表,秒级)→ 写 .data/devices.json
#   make scan-sweep  同上加 ping sweep(捞在线但 arp 表没有的设备,较慢)
#   make ui          只构建前端(esbuild → www/app.js)
#   make check       只跑 Lume 类型检查(lan.lume + src/devices.lume)
#   make watch       前端 esbuild --watch(另开终端跑 make dev / lan-dev)
#
# 依赖:
#   LUME    = ~/.local/bin/lume(release;安装 curl …/install.sh | sh,或用 ../lume-core/bin/lume-core)
#   esbuild 走 PATH,兜底 ../lume/frontend/node_modules(过渡用)
#   宿主扫描脚本用 /usr/bin/python3 + 系统 arp(无第三方依赖)

LUME      ?= $(HOME)/.local/bin/lume
PORT      ?= 8091
PY        ?= /usr/bin/python3

ESBUILD   := $(shell command -v esbuild 2>/dev/null || echo ../lume/frontend/node_modules/.bin/esbuild)

default: dev

# 停掉 PORT 上的旧服务 + 残留的 esbuild watch(供 dev / lan-dev 复用,也可单独 make stop)。
# 幂等:没有旧实例时 lsof/pgrep 返回空;每行 kill 都容忍失败(沙箱里跨会话进程可能
# 杀不动、或本就没有目标),绝不因「杀不掉/没得杀」而让 make 报 Error 中止。
stop:
	@{ lsof -ti tcp:$(PORT) 2>/dev/null; pgrep -f "lan\.lume" 2>/dev/null; } \
	| xargs -r kill 2>/dev/null || true
	@pkill -f "esbuild src/lan/app.tsx" 2>/dev/null || true
	@sleep 1
	@echo "已停 $(PORT) 上的旧服务与残留 watch(若仍占着,见下方提示)"
	@{ if [ -n "$$(lsof -ti tcp:$(PORT) 2>/dev/null)" ]; then \
	   echo "⚠️ $(PORT) 仍被占用: $$(lsof -ti tcp:$(PORT) 2>/dev/null | tr '\n' ' ')"; \
	   echo "   宿主手动: lsof -ti tcp:$(PORT) | xargs kill"; fi; } || true

# 宿主扫描:默认 arp 现成表(秒级)。LAN_SWEEP=1 加 ping sweep。
scan:
	@$(PY) scripts/scan_lan.py

scan-sweep:
	@LAN_SWEEP=1 $(PY) scripts/scan_lan.py

check:
	@$(LUME) --check lan.lume

# 入口 bundle 落 www/app.js(单入口;当前无共享 chunk)。
ui:
	@$(ESBUILD) src/lan/app.tsx --bundle --minify --format=esm --jsx=automatic --outdir=www --outbase=src/lan

watch:
	@$(ESBUILD) src/lan/app.tsx --bundle --format=esm --jsx=automatic --outdir=www --outbase=src/lan --watch

# 日常入口:先 stop(kill 旧端口服务),再 check + 构建 + 首跑快照 + 前台起服务。
# 重复执行安全——每次都先清掉上一次占着 PORT 的实例,不会撞端口。Ctrl-C 退出。
dev: stop check ui scan
	@echo "▶ http://127.0.0.1:$(PORT)  (make dev:已停旧服务再起;数据源刷新:make scan)"
	@$(LUME) lan.lume

# 向后兼容:make lan ≡ make dev
lan: dev

# dev 的 watch 版:先 stop,再 check,然后前端 --watch 与 Lume 起服务并发跑。
# 改 .tsx 即热刷;改 .lume 需 Ctrl-C 后重跑(或再 make lan-dev)。退出时清掉 watch。
lan-dev: stop check
	@trap 'pkill -f "esbuild src/lan/app.tsx" 2>/dev/null; kill 0' INT; \
	{ $(ESBUILD) src/lan/app.tsx --bundle --format=esm --jsx=automatic --outdir=www --outbase=src/lan --watch & } \
	{ $(LUME) lan.lume; }

.PHONY: default dev lan stop scan scan-sweep check ui watch lan-dev
