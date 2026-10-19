# lume-lan Makefile — 局域网设备看板(Lume 服务端 + React + 宿主扫描脚本)
#
# 本机自定义值放 .env(已 gitignore); 公开默认全部用通用示例。
-include .env
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
#   make router-fetch 抓路由器后台(家用路由器/HiLink)→ 写 .data/router.json
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
# AdGuard Home 宿主发布端口(web 控制台 + /v3 API; 容器内固定 3000, 宿主映射 ADGUARD_PORT>10000)。
# DNS 的 53 是协议标准口(设备解析指向它), 不走此变量。覆盖: make adguard-up ADGUARD_PORT=11095。
ADGUARD_PORT ?= 10095
# 镜像名 / Lume 版本(与 Dockerfile 的 ARG 一致)。
DOCKER_IMAGE ?= lume-lan
LUME_VERSION ?= v0.6.3
LUME_REPO    ?= erishen/lume
# 路由器地址默认值(docker 目标注入容器; 覆盖: make docker-run ROUTER_HOST=...)。
ROUTER_HOST  ?= 192.168.1.1
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

# 路由器后台抓取(家用路由器/HiLink):免密 deviceinfo 直接写 .data/router.json;
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

# ---- 看板 Basic Auth 凭据(10091 容器 /auth/htpasswd)----
# 生成/更新 .runtime/htpasswd(admin 用户, bcrypt cost 12, 权限 600)。
# 容器已挂载该文件(compose: HTPASSWD_FILE=/auth/htpasswd), 改完要 docker compose up -d 才生效。
# 用法:
#   make auth-pass                → 随机生成 20 位强密码并打印(不留 shell 历史)
#   make auth-pass PASSWORD=xxx   → 用指定密码(注意会出现在 shell 历史/进程列表, 慎用)
auth-pass:
	@mkdir -p .runtime
	@if [ -z "$(PASSWORD)" ]; then \
	  PASS="$$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20)"; \
	  echo "随机生成密码: $$PASS"; \
	else PASS="$(PASSWORD)"; fi; \
	/usr/sbin/htpasswd -c -b -B -C 12 .runtime/htpasswd admin "$$PASS" && \
	chmod 600 .runtime/htpasswd && \
	echo "✓ 已写入 .runtime/htpasswd (admin / $$PASS)" && \
	echo "  生效: docker compose up -d"

# ---- AdGuard Home(DNS 记录: 看「谁访问了什么域名」)----
# AdGuard 已迁到 Mac 宿主(launchd 服务 com.<user>.adguardhome, 非容器):
#   容器发布端口会把所有入站 DNS NAT 成同一网关 IP(192.168.164.1), AdGuard 看不到
#   真实设备, 看板「域名访问」永远 1 客户端; 宿主直绑 53 保留真实客户端 IP。
#   安装/数据在 ~/Applications/AdGuardHome/(二进制+conf+work), launchd 配置在
#   ~/Library/LaunchAgents/com.<user>.adguardhome.plist(登录自启, 崩溃自动拉起)。
#   make adguard-up     启宿主服务(launchd bootstrap / kickstart)
#   make adguard-down   停宿主服务(launchd bootout)
#   make adguard-log    看宿主服务日志(~/Applications/AdGuardHome/agh.log)
#   make dns-fetch dns-watch     抓一次查询日志 → .data/dns.json(配了才自动, 不配也不崩)
# 让家里设备 DNS「首选」指 <本机IP>:53(AdGuard)、「备用」填 223.5.5.5; 本机不开时设备落到备用, 不断网。
# 宿主 web 端口走 ADGUARD_PORT(默认 10095, >10000 约定); 覆盖: make adguard-up ADGUARD_PORT=11095。
# launchd 服务名: 公开默认通用示例, 本机实际名字放 .env 覆盖(ADGUARD_LABEL=com.你的用户名.adguardhome)。
ADGUARD_LABEL ?= com.<user>.adguardhome
PLIST ?= $(HOME)/Library/LaunchAgents/$(ADGUARD_LABEL).plist
adguard-up:
	@echo "▶ 起 AdGuard 宿主服务(控制台 http://127.0.0.1:$(ADGUARD_PORT), DNS 53)…"
	@launchctl bootstrap gui/$$(id -u) $(PLIST) 2>/dev/null || launchctl kickstart -k gui/$$(id -u)/$(ADGUARD_LABEL)
	@echo "  launchd 管理(登录自启); 跑 make dns-fetch dns-watch 看记录"

adguard-down:
	@launchctl bootout gui/$$(id -u)/$(ADGUARD_LABEL) 2>/dev/null || true
	@echo "已停 AdGuard 宿主服务"

adguard-log:
	@tail -f $(HOME)/Applications/AdGuardHome/agh.log

dns-fetch:
	@$(PY) scripts/adguard_fetch.py

# 实时监听 AdGuard 新客户端(定位「设备 DNS 走没走 AdGuard」):
# 跑着它, 然后在某台设备(如一台手机)上随便刷个网页, 终端立刻显示那台设备的 IP 进来没。
# 宿主 AdGuard 下: 192.168.1.x = 真实局域网设备(示例);
# 192.168.164.1 = Docker NAT 网段里其他容器的 DNS 转发流量(示例)。
dns-watch:
	@$(PY) scripts/dns_watch.py

# ---- MAC 黑名单(拉黑/解除成对; 拉黑=断网, 网关/关键设备/备注设备默认拒绝, --force 覆盖) ----
# 每个命令 1 次登录, 别连发(限流冷却 5min)。预演加 EXTRA='--dry-run'。
#   make block TARGET=192.168.1.99 EXTRA='--reason "陌生设备" --force'  (网关/关键/备注才需 --force)
#   make unblock TARGET=192.168.1.99
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

# 单元测试(标准库 unittest, 无第三方依赖): ping 平台参数 / host IP 回退 / 流量历史身份键。
test:
	@$(PY) -m unittest discover -s tests -v

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
		docker run --rm -p $(DOCKER_PORT):8091 -v $$(pwd)/.data:/app/.data:ro \
			-e ROUTER_PASS="$(ROUTER_PASS)" -e ROUTER_HOST="$(ROUTER_HOST)" $(DOCKER_IMAGE); \
	else \
		docker run --rm -p $(DOCKER_PORT):8091 -v $$(pwd)/.data:/app/.data:ro \
			-e ROUTER_HOST="$(ROUTER_HOST)" $(DOCKER_IMAGE); \
	fi

# 后台起 / 停。主 docker-compose.yml 只含 lume-lan 看板(AdGuard 已迁宿主 launchd)。
# 宿主 web 端口: 看板 DOCKER_PORT(10091); AdGuard 控制台 ADGUARD_PORT(10095), 由宿主服务提供。
docker-up:
	@[ -f .runtime/htpasswd ] || { echo "✗ .runtime/htpasswd 不存在——先 make auth-pass 生成看板凭据(compose 挂载它做 Basic Auth, 缺失会挂空目录)"; exit 1; }
	@echo "▶ 起看板容器 → http://127.0.0.1:$(DOCKER_PORT)/  (AdGuard 是宿主服务: make adguard-up)"
	@DOCKER_PORT=$(DOCKER_PORT) docker compose up -d
	@echo "  (设备 DNS 首选指 <本机IP>:53 才有域名记录; 没配 AdGuard 也不影响看板)"

docker-down:
	@docker compose down
	@echo "已停并删除容器 lume-lan"

# 一键重新部署: 刷新数据(scan + router-fetch) → 重 build 前端(ui → www/) → 打镜像 → 重启容器。
# 改完代码/数据想看容器效果就一条命令(不用再记 build + down + up 分开跑)。
# 依赖里 router-fetch 若被路由器限流也只是 stale 降级(脚本本身不失败), 安全。
docker-redeploy: scan router-fetch ui docker-build
	@$(MAKE) docker-down
	@$(MAKE) docker-up
	@echo "✅ 重新部署完成 → http://127.0.0.1:$(DOCKER_PORT)/  (宿主侧硬刷新 Cmd+Shift+R)"

# compose 方式(主 compose 现在含 lume-lan + adguard 两个服务, 等价于 make docker-up)。
# 端口经 env 传入(DOCKER_PORT + ADGUARD_PORT; compose 自动读 .env 做默认值替换)。
docker-compose:
	@DOCKER_PORT=$(DOCKER_PORT) ADGUARD_PORT=$(ADGUARD_PORT) docker compose up -d

docker-compose-down:
	@docker compose down

.PHONY: default dev lan lan-dev stop scan scan-sweep check ui watch router-fetch notify-test daily-digest \
         adguard-up adguard-down adguard-log dns-fetch dns-watch \
         block block-list unblock \
         docker-fetch docker-build docker-build-multiarch docker-run docker-up docker-down docker-redeploy docker-compose docker-compose-down
