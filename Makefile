# lume-lan Makefile — 局域网设备看板(Lume 服务端 + React + 宿主扫描脚本)
#
#   make scan      跑宿主扫描脚本(arp 现成表,秒级)→ 写 .data/devices.json
#   make scan-sweep 同上加 ping sweep(捞在线但 arp 表没有的设备,较慢)
#   make ui        只构建前端(esbuild → www/app.js)
#   make check     只跑 Lume 类型检查(lan.lume + src/devices.lume)
#   make lan       check + ui + scan + 起服务(默认目标)
#   make watch     前端 esbuild --watch(另开终端跑 make lan)
#
# 依赖:
#   LUME  = ~/.local/bin/lume(release;安装 curl …/install.sh | sh,或用 ../lume-core/bin/lume-core)
#   esbuild 走 PATH,兜底 ../lume/frontend/node_modules(过渡用)
#   宿主扫描脚本用 /usr/bin/python3 + 系统 arp(无第三方依赖)

LUME      ?= $(HOME)/.local/bin/lume
PORT      ?= 8091
PY        ?= /usr/bin/python3

ESBUILD   := $(shell command -v esbuild 2>/dev/null || echo ../lume/frontend/node_modules/.bin/esbuild)

IMAGE     ?= lume-lan:latest
CNAME     ?= lume-lan

default: lan

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

# 起服务:清掉 8091 上的旧实例,check + 前端产物 + 首跑快照,然后跑 lan.lume。
lan: check ui scan
	@{ lsof -ti tcp:$(PORT) 2>/dev/null; pgrep -f "lan\.lume" 2>/dev/null; } \
	| xargs -r kill 2>/dev/null; sleep 1
	@echo "▶ http://127.0.0.1:$(PORT)  (刷新数据源:make scan)"
	@$(LUME) lan.lume

# dev:check + 前端 watch + 起服务。改 .tsx 即刷,改 .lume 重启生效。
lan-dev: check
	@{ lsof -ti tcp:$(PORT) 2>/dev/null; pgrep -f "lan\.lume" 2>/dev/null; } \
	| xargs -r kill 2>/dev/null; sleep 1
	@trap 'pkill -f "esbuild src/lan/app.tsx" 2>/dev/null; kill 0' INT
	@{ $(ESBUILD) src/lan/app.tsx --bundle --format=esm --jsx=automatic --outdir=www --outbase=src/lan --watch & } \
	@{ $(LUME) lan.lume; }

.PHONY: default scan scan-sweep check ui watch lan lan-dev
