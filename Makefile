# lume-lan Makefile — 局域网设备看板(Lume 服务端 + React + 宿主扫描脚本)
#
# 常用:
#   make dev       日常入口(热更):先 stop 清掉 PORT 旧服务/残留 watch,再 check+scan,
#                  然后 esbuild --watch(改 .tsx 即自动重打包,毫秒级)+ 前台起 Lume。
#                  前台阻塞,Ctrl-C 退出时自动清掉 esbuild watch;重复执行先清旧实例,不撞端口。
#   make stop      只停:kill PORT 上旧服务 + 残留 esbuild watch(不重启)
#   make lan       make dev 的别名(向后兼容)
#   make lan-dev   make dev 的别名(原「watch 版」已并入 dev)
#
# 分步:
#   make scan        跑宿主扫描脚本(arp 现成表,秒级)→ 写 .data/devices.json
#   make scan-sweep  同上加 ping sweep(捞在线但 arp 表没有的设备,较慢)
#   make router-fetch 抓路由器后台(华为 WS5102/HiLink)→ 写 .data/router.json
#                  (免密的 deviceinfo 直接可用;终端/流量需 .env 填 ROUTER_PASS,命中候选端点)
#   make ui          一次性构建前端(esbuild 单次 → www/app.js,非 watch;dev 用的是 watch)
#   make check       只跑 Lume 类型检查(lan.lume + src/devices.lume)
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

# 路由器后台抓取(华为 WS5102/HiLink):免密 deviceinfo 直接写 .data/router.json;
# 终端/流量需 .env 填 ROUTER_PASS(与 ROUTER_TERMINAL_API,命中候选端点)。取不到不影响 arp 快照。
router-fetch:
	@$(PY) scripts/router_fetch.py

check:
	@$(LUME) --check lan.lume

# 入口 bundle 落 www/app.js(单入口;当前无共享 chunk)。
ui:
	@$(ESBUILD) src/lan/app.tsx --bundle --minify --format=esm --jsx=automatic --outdir=www --outbase=src/lan

watch:
	@$(ESBUILD) src/lan/app.tsx --bundle --format=esm --jsx=automatic --outdir=www --outbase=src/lan --watch

# 日常入口(热更):stop(清旧实例)→ check → scan → router-fetch → esbuild --watch(后台)+ 前台起 Lume。
# 重复执行安全——每次先清掉占 PORT 的上次实例;改 .tsx 秒级热刷;改 .lume 需 Ctrl-C 重跑。
# 注意(macOS make 默认 /bin/sh = POSIX sh):不能用 "{ cmd & }" 花括号组(& 后紧跟 } 非法)。
# 故 esbuild watch 单独一行后台(&),Lume 单独一行前台阻塞;Ctrl-C 停 Lume 后,残留的
# esbuild watch 由「make stop」或「下次 make dev」的 stop 阶段清掉(stop 里已 pkill esbuild)。
dev: stop check scan router-fetch
	@echo "▶ http://127.0.0.1:$(PORT)  (make dev:热更已开,.tsx 改动自动重打包;数据源刷新:make scan)"
	@$(ESBUILD) src/lan/app.tsx --bundle --format=esm --jsx=automatic --outdir=www --outbase=src/lan --watch &
	@echo "▶ esbuild watch 已后台;Lume 起服务中(Ctrl-C 停 Lume,残留 watch 由 make stop 清理)"
	@$(LUME) lan.lume

# 向后兼容:make lan ≡ make dev
lan: dev

# lan-dev 原为「watch 版 dev」,现已并入 make dev(热更是默认行为),此处仅作别名。
lan-dev: dev

.PHONY: default dev lan lan-dev stop scan scan-sweep check ui watch router-fetch
