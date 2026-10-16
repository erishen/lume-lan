# lume-lan Makefile — 局域网设备看板(Lume 服务端 + React + 宿主扫描脚本)
#
# 常用:
#   make dev       日常入口(热更):先 stop 清掉 PORT 旧服务/残留 watch/数据循环,再 check+scan,
#                  然后 esbuild --watch(改 .tsx 即自动重打包,毫秒级)+ 后台数据刷新循环
#                  (每 REFRESH_SECS=60s 重跑 scan + router-fetch,让 UI「刷新」按钮读到新快照;
#                  pid 落 .runtime/refresh.pid,make stop / 下次 make dev 自动清掉)+ 前台起 Lume。
#                  前台阻塞;重复执行先清旧实例,不撞端口。
#   make stop      只停:kill PORT 上旧服务 + 残留 esbuild watch + 数据刷新循环(不重启)
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
# docker 容器在宿主的发布端口(>10000, 避开本地 make dev 的 8091); 容器内 Lume 仍绑 8091。
DOCKER_PORT ?= 10091
# 镜像名 / Lume 版本(与 Dockerfile 的 ARG 一致)。
DOCKER_IMAGE ?= lume-lan
LUME_VERSION ?= v0.6.3
LUME_REPO    ?= erishen/lume
# 路由器地址默认值(docker 目标注入容器; 覆盖: make docker-run ROUTER_HOST=...)。
ROUTER_HOST  ?= 192.168.3.1
PY        ?= /usr/bin/python3

ESBUILD   := $(shell command -v esbuild 2>/dev/null || echo ../lume/frontend/node_modules/.bin/esbuild)

default: dev

# 数据自动刷新间隔(秒)，可用 REFRESH_SECS=30 make dev 覆盖。
REFRESH_SECS ?= 60

# 停掉 PORT 上的旧服务 + 残留 esbuild watch + 数据刷新循环(供 dev / lan-dev 复用,也可单独 make stop)。
stop:
	@{ lsof -ti tcp:$(PORT) 2>/dev/null; pgrep -f "lan\.lume" 2>/dev/null; } \
	| xargs -r kill 2>/dev/null || true
	@pkill -f "esbuild src/lan/app.tsx" 2>/dev/null || true
	@cat .runtime/refresh.pid 2>/dev/null | xargs -r kill 2>/dev/null || true
	@rm -f .runtime/refresh.pid
	@sleep 1
	@echo "已停 $(PORT) 上的旧服务、残留 watch 与数据刷新循环(若仍占着,见下方提示)"
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

# 发一条测试消息到 webhook(验证 .env 的 WEBHOOK_URL/SECRET 配对了)。
# 告警(新设备进网/WAN 跳变/异常流量)在 make router-fetch / dev 数据循环里自动评估,
# 配了 webhook 就推群里, 没配只打印。
notify-test:
	@$(PY) scripts/webhook_notify.py "lume-lan webhook 测试: 收到这条说明配好了 ✓"

# 立即推一条日报(在线/活跃台数 + 自上次日报以来流量 Top5 + WAN 状态)。
# 不手动推的话, 数据循环每 24h 自动推一条(maybe_send 判定)。
daily-digest:
	@$(PY) scripts/daily_digest.py

# ---- AdGuard Home(DNS 记录: 看「谁访问了什么域名」)----
# 起 AdGuard 容器(独立 compose 文件; 首次进 http://127.0.0.1:3000 向导):
#   make adguard-up      起容器(3000=控制台/API, 53=DNS)
#   make adguard-down    停容器(配置持久化在 .data/adguard/)
#   make dns-fetch       抓一次查询日志 → .data/dns.json(配了才自动, 不配也不崩)
#   make adguard-log     看 AdGuard 容器日志
# 让家里设备 DNS「首选」指 <本机IP>:53(AdGuard)、「备用」填 223.5.5.5; 本机不开时设备落到备用, 不断网。
adguard-up:
	@echo "▶ 起 AdGuard Home(3000=控制台, 53=DNS)…"
	@docker compose -f adguard.home.yml up -d
	@echo "  首次请进 http://127.0.0.1:3000 向导(或直接默认保存); 起完跑 make dns-fetch 看记录"

adguard-down:
	@docker compose -f adguard.home.yml down

adguard-log:
	@docker compose -f adguard.home.yml logs -f adguard

dns-fetch:
	@$(PY) scripts/adguard_fetch.py

# ---- MAC 黑名单(拉黑/解除成对; 拉黑=断网, 网关/关键设备/备注设备默认拒绝, --force 覆盖) ----
# 每个命令 1 次登录, 别连发(限流冷却 5min)。预演加 EXTRA='--dry-run'。
#   make block TARGET=192.168.3.99 EXTRA='--reason "陌生设备" --force'  (网关/关键/备注才需 --force)
#   make unblock TARGET=192.168.3.99
#   make block-list
EXTRA ?=
block:
	@[ -n "$(TARGET)" ] || { echo "用法: make block TARGET=IP或MAC [EXTRA='--reason ... --force']"; exit 1; }
	@$(PY) scripts/mac_block.py add $(TARGET) $(EXTRA)

block-list:
	@$(PY) scripts/mac_block.py list

unblock:
	@[ -n "$(TARGET)" ] || { echo "用法: make unblock TARGET=IP或MAC"; exit 1; }
	@$(PY) scripts/mac_block.py remove $(TARGET) $(EXTRA)

check:
	@$(LUME) --check lan.lume

# 入口 bundle 落 www/app.js(单入口;当前无共享 chunk)。
ui:
	@$(ESBUILD) src/lan/app.tsx --bundle --minify --format=esm --jsx=automatic --outdir=www --outbase=src/lan

watch:
	@$(ESBUILD) src/lan/app.tsx --bundle --format=esm --jsx=automatic --outdir=www --outbase=src/lan --watch

# 日常入口(热更):stop(清旧实例)→ check → scan → router-fetch → esbuild --watch(后台)
# + 数据刷新循环(后台每 REFRESH_SECS 秒重跑 scan+router-fetch+dns-fetch,让「刷新」按钮能拿到新快照;
#   子 shell pid 落 .runtime/refresh.pid,make stop 用它清掉)→ 前台起 Lume。
# 重复执行安全——每次先清掉占 PORT 的上次实例。
# 注意(macOS make 默认 /bin/sh = POSIX sh):不能用 "{ cmd & }" 花括号组(& 后紧跟 } 非法);
# 数据循环用 { (while...done) & echo $$! > file; } 合法形式(同一行,子 shell 后台启动后立即捕获 PID);
# Ctrl-C 停 Lume 后,残留的 esbuild watch 和数据循环由「make stop」或「下次 make dev」清掉。
dev: stop check scan router-fetch
	@echo "▶ http://127.0.0.1:$(PORT)  (make dev:热更已开,.tsx 改动自动重打包)"
	@$(ESBUILD) src/lan/app.tsx --bundle --format=esm --jsx=automatic --outdir=www --outbase=src/lan --watch &
	@mkdir -p .runtime; { ( while true; do sleep $(REFRESH_SECS); $(PY) scripts/scan_lan.py >/dev/null 2>&1 || true; $(PY) scripts/router_fetch.py >/dev/null 2>&1 || true; $(PY) scripts/adguard_fetch.py >/dev/null 2>&1 || true; done ) & echo $$! > .runtime/refresh.pid; }
	@echo "▶ esbuild watch 后台运行;数据每 $(REFRESH_SECS)s 自动刷新(pid $$(cat .runtime/refresh.pid))"
	@echo "▶ Lume 起服务中(Ctrl-C 停;残留 watch/数据循环由 make stop 清理)"
	@$(LUME) lan.lume

# 向后兼容:make lan ≡ make dev
lan: dev

# lan-dev 原为「watch 版 dev」,现已并入 make dev(热更是默认行为),此处仅作别名。
lan-dev: dev

# ---- Docker 目标(容器内跑 Lume serve + 读 .data/ 快照; 宿主发布端口 >10000 避开本地 8091) ----
# 容器内 Lume 固定绑 8091; 宿主侧映射 DOCKER_PORT(默认 10091)。arp 快照靠宿主 make scan
# 写进 .data/, 容器挂载读取; 可选 REFRESH=1 让容器内每 60s 自刷路由器(打 ROUTER_HOST)。
# 凭据: docker run/compose 时注入 ROUTER_PASS(不进镜像)。
#
# Lume 二进制策略(根治 OrbStack 容器内 curl 走网关 TLS 被截断 exit 35):
#   make docker-fetch  → 宿主按本机 arch 把 static tarball 下到 vendor/(宿主网络通)
#   docker build       → COPY vendor/ 进镜像, 有匹配 arch 就离线安装, 没有才容器内下载兜底
#   所以 make docker-build 先依赖 docker-fetch, 本地 arm64 单机 build 全程不用容器内联网。

# 宿主预下载 Lume 全静态 linux 二进制到 vendor/(按本机 arch)。幂等: 已存在且校验和匹配则跳过。
# 用独立脚本承载下载(脚本里 $1/sha256sum 不经 make recipe 转义, 干净); Makefile 只传 VERSION/REPO。
docker-fetch:
	@sh scripts/fetch_lume_vendor.sh "$(LUME_VERSION)" "$(LUME_REPO)"

# 打镜像(先宿主预下载 Lume 二进制, 再 build; build 时 COPY vendor/ 离线安装)。
# 多架构: 见下方 docker-build-multiarch(它需 vendor/ 同时含两种 arch, 可用 LUME_VERSION 指定)。
docker-build: docker-fetch
	@echo "▶ 打镜像 $(DOCKER_IMAGE)(LUME_VERSION=$(LUME_VERSION), 离线用 vendor/)…"
	@docker build --build-arg LUME_VERSION=$(LUME_VERSION) -t $(DOCKER_IMAGE) .

# 多架构镜像(amd64+arm64)。需先 docker buildx create --use(或已用默认 builder)。
docker-build-multiarch:
	@docker buildx build --platform linux/arm64,linux/amd64 \
		--build-arg LUME_VERSION=$(LUME_VERSION) -t $(DOCKER_IMAGE) .

# 前台起容器(宿主 http://127.0.0.1:$(DOCKER_PORT))。Ctrl-C 停。先宿主出 .data/。
# 传 ROUTER_PASS 才注入凭据(make 变量展开, 不进镜像): make docker-run ROUTER_PASS='...'
docker-run: scan router-fetch
	@echo "▶ http://127.0.0.1:$(DOCKER_PORT)/  (宿主:$(DOCKER_PORT) → 容器:8091; .data/ 已挂载)"
	@if [ -n "$(ROUTER_PASS)" ]; then \
		docker run --rm -p $(DOCKER_PORT):8091 -v $$(pwd)/.data:/app/.data \
			-e ROUTER_PASS="$(ROUTER_PASS)" -e ROUTER_HOST="$(ROUTER_HOST)" $(DOCKER_IMAGE); \
	else \
		docker run --rm -p $(DOCKER_PORT):8091 -v $$(pwd)/.data:/app/.data \
			-e ROUTER_HOST="$(ROUTER_HOST)" $(DOCKER_IMAGE); \
	fi

# 后台起 / 停。用固定容器名, 便于 make docker-down 清理。
docker-up:
	@echo "▶ 后台起 $(DOCKER_IMAGE) → http://127.0.0.1:$(DOCKER_PORT)/"
	@docker rm -f lan-lan 2>/dev/null || true
	@docker run -d --name lan-lan -p $(DOCKER_PORT):8091 -v $$(pwd)/.data:/app/.data \
		-e ROUTER_HOST="$(ROUTER_HOST)" $(DOCKER_IMAGE)

docker-down:
	@docker stop lan-lan 2>/dev/null && docker rm lan-lan 2>/dev/null || true
	@echo "已停并删除容器 lan-lan"

# 一键重新部署: 刷新数据(scan + router-fetch) → 重 build 前端(ui → www/) → 打镜像 → 重启容器。
# 改完代码/数据想看容器效果就一条命令(不用再记 build + down + up 分开跑)。
# 依赖里 router-fetch 若被路由器限流也只是 stale 降级(脚本本身不失败), 安全。
docker-redeploy: scan router-fetch ui docker-build
	@$(MAKE) docker-down
	@$(MAKE) docker-up
	@echo "✅ 重新部署完成 → http://127.0.0.1:$(DOCKER_PORT)/  (宿主侧硬刷新 Cmd+Shift+R)"

# compose 方式(等价于 docker-up, 走 docker-compose.yml)。DOCKER_PORT 经 env 传入。
docker-compose:
	@DOCKER_PORT=$(DOCKER_PORT) docker compose up -d

docker-compose-down:
	@docker compose down

.PHONY: default dev lan lan-dev stop scan scan-sweep check ui watch router-fetch notify-test daily-digest \
         adguard-up adguard-down adguard-log dns-fetch \
         block block-list unblock \
         docker-fetch docker-build docker-build-multiarch docker-run docker-up docker-down docker-redeploy docker-compose docker-compose-down
