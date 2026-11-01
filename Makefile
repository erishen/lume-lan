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

# 优先用同仓库编译出来的 lume(../lume/bin/lume), 其次 ~/.local/bin。
# 为什么: router_fetch.run.lume 依赖 http_* 返回 headers 才能维持会话,
# 而 ~/.local/bin/lume 可能是没这个能力的旧版(实测 0.8.1)。用旧版不会
# 报错, 只会"登录成功但拿不到终端"——症状完全指不到版本。
LUME      ?= $(if $(wildcard ../lume/bin/lume),../lume/bin/lume,$(HOME)/.local/bin/lume)
PORT      ?= 8091
# docker 容器在宿主的发布端口(>10000, 避开本地 make dev 的 8091); 容器内 Lume 仍绑 8091。
DOCKER_PORT ?= 10091
# AdGuard Home 宿主发布端口(web 控制台 + /v3 API; 容器内固定 3000, 宿主映射 ADGUARD_PORT>10000)。
# DNS 的 53 是协议标准口(设备解析指向它), 不走此变量。覆盖: make adguard-up ADGUARD_PORT=11095。
ADGUARD_PORT ?= 10095
# 镜像名 / Lume 版本(与 Dockerfile 的 ARG 一致)。
DOCKER_IMAGE ?= lume-lan
LUME_VERSION ?= v0.12.1
LUME_REPO    ?= erishen/lume
# 路由器地址(容器目标注入容器用)。**不写死网段**: 空 = 让脚本自己从
# .data/devices.json 的 gateway 推导(宿主 arp 扫出来的真实网关);
# 显式覆盖: make docker-run ROUTER_HOST=192.168.1.1(或 .env 里配)。
ROUTER_HOST  ?=
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
	@# 用 lume 版: 宿主侧不必再依赖 python3 跑这一项(容器里已完全去掉)
	@ROUTER_HOST="$${ROUTER_HOST:-$$(sed -n 's/^ROUTER_HOST=//p' .env | head -1)}" \
	 ROUTER_USER="$${ROUTER_USER:-$$(sed -n 's/^ROUTER_USER=//p' .env | head -1)}" \
	 ROUTER_PASS="$${ROUTER_PASS:-$$(sed -n 's/^ROUTER_PASS=//p' .env | head -1)}" \
	 $(LUME) scripts/router_fetch.run.lume

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

# ---- 可互访的 lume peer 节点(前端「应用互访」Tab)----
# 探测为什么在宿主跑: 看板容器在 Docker NAT 网段, 连不到宿主 loopback 上的
# peer 端口; PEER_SHARED 也不该进容器环境。与 scan_lan.py 同一套分工。
# .env 配: PEER_SHARED(与对端一致) + PEER_TARGETS="127.0.0.1:18152,192.168.1.20:18088"
peers-fetch:
	@$(PY) scripts/peers_fetch.py

# peer 快照常驻采集: 每 PEER_REFRESH_SECS(默认 60) 秒一轮, 且看板审批后
# (.data/.peers-refresh 标记)立刻重采 —— 让「批准/驳回」点完就能看到结果。
peers-watch:
	@PEER_REFRESH_SECS=$(REFRESH_SECS) $(PY) scripts/peers_fetch.py --loop

# ---- A2A 长任务 worker ----
# lume 没有 spawn/exec, http_* 也是同步阻塞 —— 「提交后立刻返回、后台慢慢跑」
# 这件事在纯 lume 里做不到, 必须由这个宿主进程把任务从 submitted 推到完成。
#   make a2a-worker        常驻(每 2s 扫一轮)
#   make a2a-worker-once   只跑一轮(调试 / CI)
a2a-worker:
	@echo "▶ A2A worker 常驻(扫 .data/a2a-tasks, 每 2s 一轮)"
	@$(PY) scripts/a2a_worker.py

a2a-worker-once:
	@$(PY) scripts/a2a_worker.py --once

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

# 隐私检查(仓库公开): 已跟踪文件里不得出现真实局域网标识。
# 硬规则扫私有网段/公网 IP/MAC(示例值白名单放行); 加 LOCAL=1 再比对本机 .data/ 实况。
# 默认扫「已跟踪 + 未跟踪(未被 .gitignore 忽略)」——未跟踪那部分很关键:
# 新写的文件最容易带进真实网段, 而旧实现只查 git ls-files, 等于提交前
# 完全看不见它们。加 TRACKED=1 可退回只查已跟踪的旧行为。
privacy:
	@$(PY) scripts/check_privacy.py $(if $(LOCAL),--local,) $(if $(TRACKED),--tracked,)

# ---- 跨机 peer 通讯(见 docs/peer.md) ----
# 起本节点: make peer-serve(读 .env 的 PEER_SHARED/PEER_NAME/PEER_BIND/PEER_PORT)
peer-serve: check-peer
	@$(LUME) peer_node.lume

# 本节点的「带 agent」形态: agent-httpd(被 /peer/ask 调用) + peer_node(对外端点)。
#
# 为什么需要单独一个目标: 只有 peer_node 时 /peer/ask 会回 404 —— 它背后
# 没有 agent 进程。看板那边于是探测到 agent=false, 不显示提问框, 看起来像
# "这个功能没做"。两个进程必须一起起。
#
# 用的是**仓库里的 lume**(../lume/bin/lume), 不是 ~/.local/bin/lume:
# 后者是 10 月 23 日的旧版本, 与工作区源码不一致。之前踩过 —— 旧版常驻着,
# 新版起来后两个实例争同一个端口和同一个 db 文件, 数据时而正常时而全 0。
LUME_REPO_BIN ?= ../lume/bin/lume
AGENT_HTTPD   ?= ../lume/agent-httpd/bin/agent-httpd
AGENT_PORT    ?= 18099
PEER_PORT_RUN ?= 18092
# 绑定地址。默认 127.0.0.1(单机安全基线: 只有本机能连)。
#
# 跨机时必须设 0.0.0.0 —— 但那个决定不该藏在 Makefile 里: 之前这里是硬编码
# 127.0.0.1, 于是 .env 里写 PEER_BIND=0.0.0.0 完全不生效, 而文档却说"配
# PEER_BIND 即可"。这种"配了不生效"比"不支持"更难排查(实测另一台机器
# 连过来只得到连接被拒, 看起来像网络问题)。
#
# 现在: .env 的 PEER_BIND 优先, 没写才用 127.0.0.1。
PEER_BIND_RUN ?= $(shell grep -E '^PEER_BIND=' .env 2>/dev/null | cut -d= -f2- | sed 's/^$$/127.0.0.1/')
PEER_BIND_RUN := $(if $(PEER_BIND_RUN),$(PEER_BIND_RUN),127.0.0.1)
# /a2a 与 /peer/ask 里等 agent 答完的上限。
#
# 默认 90s 不够: lan 的 agent 挂了 peers MCP(18 个跨节点工具), 模型要在里面
# 挑一个再综合, 实测单次 90s 打满仍失败。数据节点(crm/scanner)没这个问题,
# 因为它们先走关键词匹配, 认不出才委派 —— 而 lan 每句都问 agent。
PEER_AGENT_TIMEOUT ?= 240

peer-serve-agent: check-peer
	@if [ ! -x "$(LUME_REPO_BIN)" ]; then \
	  echo "✗ 找不到 $(LUME_REPO_BIN) —— 先 cd ../lume && make" >&2; exit 1; fi
	@if [ ! -x "$(AGENT_HTTPD)" ]; then \
	  echo "✗ 找不到 $(AGENT_HTTPD) —— 先 cd ../lume/agent-httpd && make" >&2; exit 1; fi
	@# agent 只绑 loopback: 它没有自带的鉴权, 暴露到网卡上是事故。
	@# peer_node 才是对外端点(有签名校验)。
	@echo "▶ agent-httpd :$(AGENT_PORT) (loopback)"
	@AGENT_URL=http://127.0.0.1:$(AGENT_PORT) \
	  $(AGENT_HTTPD) -p $(AGENT_PORT) -w 2 & \
	 sleep 1; \
	 echo "▶ peer_node :$(PEER_PORT_RUN)"; \
	 AGENT_URL=http://127.0.0.1:$(AGENT_PORT) \
	   $(LUME_REPO_BIN) peer_node.lume; \
	 kill %1 2>/dev/null || true

# 探测对端: make peer-probe PEER_HOST=192.168.1.20:18092
peer-probe: check-peer
	@$(LUME) peer_probe.lume

# 语法检查用**仓库里的** lume: 旧版 ~/.local/bin/lume(10-23)不认识新的
# 语法/类型规则, 用它检查会漏报或误报, 让 --check 失去意义。
check-peer:
	@if [ -x "$(LUME_REPO_BIN)" ]; then $(LUME_REPO_BIN) --check peer_node.lume; \
	 else $(LUME) --check peer_node.lume; fi
	@if [ -x "$(LUME_REPO_BIN)" ]; then $(LUME_REPO_BIN) --check peer_probe.lume; \
	 else $(LUME) --check peer_probe.lume; fi

# 后台常驻本节点的 peer 端点 + agent(供看板「应用互访」的提问框使用)。
#
# 与 peer-serve-agent 的区别: 那个是前台(调试时看得见日志); 这个是后台,
# 日志落 .runtime/, 适合长期挂着让看板随时能问。
#
# 注意它**不会**自动改 PEER_TARGETS: 探测列表在 .env 里, 由人决定要探谁。
# 起完之后需要在 .env 的 PEER_TARGETS 里加上 127.0.0.1:$(PEER_PORT_RUN)。
# 起本节点(agent-httpd + peer_node)。
#
# agent-httpd 必须继承 PEER_* : 它拉起的 peer_bridge.py(MCP) 靠这些变量去
# 探测各节点。不传的话 bridge 探测到 0 个节点, agent 只拿到 _peers__discover
# 一个空壳工具 —— 表面"接上了", 实际什么也调不了。
# 起本节点(agent-httpd + peer_node)。
# 顺带把自己的名字写进共享自注册目录 —— 见 scripts/peer_registry.py。
# 这样别的节点不需要为"多了一个应用"改它们自己的白名单。
peer-up: peer-register
	@mkdir -p .runtime
	@if pgrep -f "agent-httpd -p $(AGENT_PORT)" >/dev/null 2>&1; then \
	  echo "  agent-httpd :$(AGENT_PORT) 已在运行"; \
	 else \
	  AGENT_URL=http://127.0.0.1:$(AGENT_PORT) \
	  PEER_NAME=$$(grep -E '^PEER_NAME=' .env | cut -d= -f2) \
	  PEER_SHARED=$$(grep -E '^PEER_SHARED=' .env | cut -d= -f2) \
	  PEER_ROOT=$$(grep -E '^PEER_ROOT=' .env | cut -d= -f2) \
	  PEER_TARGETS=$$(grep -E '^PEER_TARGETS=' .env | cut -d= -f2) \
	  AGENT_TIMEOUT=$(PEER_AGENT_TIMEOUT) \
	  LLM_API_URL=$$(grep -E '^LLM_API_URL=' .env | cut -d= -f2-) \
	  LLM_MODEL=$$(grep -E '^LLM_MODEL=' .env | cut -d= -f2-) \
	  LLM_API_KEY=$$(grep -E '^LLM_API_KEY=' .env | cut -d= -f2-) \
	    nohup $(AGENT_HTTPD) -p $(AGENT_PORT) -w 2 > .runtime/agent-httpd.log 2>&1 & \
	  echo "  已启动 agent-httpd :$(AGENT_PORT) → .runtime/agent-httpd.log"; fi
	@sleep 1
	@# 按**端口**判断, 不是按进程名: "peer_node.lume" 这个模式会同时匹配到
	@# blog/crm/scanner 的进程, 于是这里误判"已在运行"而跳过启动, 18092 一直
	@# 空着 —— 表现为看板的提问框不见了, 却查不出谁没起。
	@if lsof -nP -iTCP:$(PEER_PORT_RUN) -sTCP:LISTEN >/dev/null 2>&1; then \
	  echo "  peer_node :$(PEER_PORT_RUN) 已在监听"; \
	 else \
	  AGENT_URL=http://127.0.0.1:$(AGENT_PORT) \
	  PEER_NAME=$$(grep -E '^PEER_NAME=' .env | cut -d= -f2) \
	  PEER_SHARED=$$(grep -E '^PEER_SHARED=' .env | cut -d= -f2) \
	  PEER_ROOT=$$(grep -E '^PEER_ROOT=' .env | cut -d= -f2) \
	  PEER_PORT=$(PEER_PORT_RUN) PEER_BIND=$(PEER_BIND_RUN) \
	    nohup $(LUME_REPO_BIN) peer_node.lume > .runtime/peer_node.log 2>&1 & \
	  echo "  已启动 peer_node :$(PEER_PORT_RUN) → .runtime/peer_node.log"; fi
	@sleep 2
	@echo "▶ 验证: python3 scripts/peers_fetch.py 后看 .data/peers.json 的 agent 字段"

# 只停**本节点**, 不碰别的应用。
#
# 踩过的坑: 原来这里写 `pkill -f "peer_node.lume"`, 它按进程名匹配 ——
# blog/crm/scanner 的节点进程名长得一模一样, 于是一起被杀。表现是
# "make peer-down 之后看板里的对端全变不可达", 而日志里只有一句
# 已停止, 完全看不出波及了别人。
#
# 现在按**端口**定位本节点(18092), 用 lsof 取出 pid 再杀。
peer-down:
	@if lsof -nP -iTCP:$(PEER_PORT_RUN) -sTCP:LISTEN >/dev/null 2>&1; then \
	  lsof -nP -tiTCP:$(PEER_PORT_RUN) -sTCP:LISTEN | xargs kill 2>/dev/null || true; \
	  echo "  已停 peer_node :$(PEER_PORT_RUN)"; \
	 else echo "  peer_node :$(PEER_PORT_RUN) 未在运行"; fi
	@if lsof -nP -iTCP:$(AGENT_PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
	  lsof -nP -tiTCP:$(AGENT_PORT) -sTCP:LISTEN | xargs kill 2>/dev/null || true; \
	  echo "  已停 agent-httpd :$(AGENT_PORT)"; \
	 else echo "  agent-httpd :$(AGENT_PORT) 未在运行"; fi

# 本节点(带 agent)的启停用 peer-up / peer-down。**别的应用不在这里起** ——
# 每个应用自己管自己的 peer_node.lume(各自仓库有 make peer-up)。
#
# 早期这里有个 peer-up-apps 会去拉起 blog/crm/scanner 的进程, 那是错的:
#   * 版本漂移 —— 对端改了 peer_node.lume, 看板不知道, 也不会重启它;
#   * 配置漂移 —— 端口/数据路径变了, 看板里硬编码的那份信息就过时了;
#   * 权限错位 —— 一个应用凭什么能起另一个应用的进程?
# 看板的职责只是**探**和**展示**。
#
# 探不到节点时, 提示去哪起, 而不是替它起。
# 常驻采集: 每 REFRESH_SECS 秒重采一次快照, 有刷新标记(审批后写的)立刻采。
#
# 为什么需要它: 没有常驻采集时快照只在手动 make peers-fetch 时更新, 页面
# 会一直显示几小时前的数据 —— 节点掉线也看不出来, 而这种"数据是旧的"
# 从界面上完全看不出来(除非去看 age_secs)。
peer-watch:
	@mkdir -p .runtime
	@if pgrep -f "peers_fetch.py --loop" >/dev/null 2>&1; then \
	  echo "  peer 采集已在运行"; \
	 else \
	  nohup sh -c 'PEER_REFRESH_SECS=$(REFRESH_SECS) $(PY) scripts/peers_fetch.py --loop' \
	    > .runtime/peers-watch.log 2>&1 & \
	  echo $$! > .runtime/peers-watch.pid; \
	  echo "  已启动 peer 采集(每 $(REFRESH_SECS)s) → .runtime/peers-watch.log"; fi

peer-watch-stop:
	@pkill -f "peers_fetch.py --loop" 2>/dev/null && echo "  已停止 peer 采集" \
	  || echo "  peer 采集未在运行"

# 自注册: 把本节点写进 .data/peers.d/<name>.json。
# 别的节点靠扫这个目录认人(identity.lume 的 known 会先查目录)。
peer-register:
	@$(PY) scripts/peer_registry.py register \
	  --name "$$(grep -E '^PEER_NAME=' .env | cut -d= -f2-)" \
	  --port $(PEER_PORT_RUN) 2>/dev/null || \
	  echo "  (自注册跳过: 无法确定 PEER_NAME)"

peer-registry:
	@$(PY) scripts/peer_registry.py list

peer-hint:
	@echo "▶ 各应用的 peer 节点由它自己启动:"
	@echo "    cd ../lume-blog    && make peer-up   # :18321"
	@echo "    cd ../lume-crm     && make peer-up   # :18102 (或 make docker-peer-up 用容器)"
	@echo "    cd ../lume-scanner && make peer-up   # :18103 (或 make docker-peer-up 用容器)"
	@echo "  然后: make peers-fetch"

peer-status:
	@a=$$(pgrep -f 'agent-httpd -p $(AGENT_PORT)' | head -1); \
	 echo "agent-httpd :$(AGENT_PORT)  $${a:-未运行}"; \
	 p=$$(pgrep -f 'peer_node.lume' | head -1); \
	 echo "peer_node   :$(PEER_PORT_RUN) $${p:-未运行}"

# 安装 git 钩子: 提交前自动跑 privacy --staged(只查本次暂存的改动)。
hooks:
	@printf '#!/bin/sh\ncd "$$(git rev-parse --show-toplevel)" || exit 1\nexec %s scripts/check_privacy.py --staged\n' "$(PY)" > .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@echo "✓ 已安装 .git/hooks/pre-commit(提交前跑 privacy --staged)"

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
	@mkdir -p .runtime; { ( while true; do sleep $(REFRESH_SECS); $(PY) scripts/scan_lan.py >/dev/null 2>&1 || true; $(PY) scripts/router_fetch.py >/dev/null 2>&1 || true; $(PY) scripts/adguard_fetch.py >/dev/null 2>&1 || true; $(PY) scripts/peers_fetch.py >/dev/null 2>&1 || true; done ) & echo $$! > .runtime/refresh.pid; }
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

# 用**工作区源码**重编静态 Linux lume 打进 vendor/(不经 GitHub release)。
#
# 什么时候用: 上游 release 落后于本地修复时。踩过的实例 —— fork 前 TLS 预热
# (lume_http_warmup)是本地新增的, 上游 v0.11.0 里没有; 用 release 二进制打镜像,
# 容器里所有 A2A 出站首次 HTTPS 都会失败, 而源码怎么看都是对的。
#
# 产物额外写 vendor/.lume-build 记录来源/commit/未提交改动数 —— 本地构建无法用
# 上游 digest 校验, 只能靠这份元数据追溯。.lume-version 仍是 v0.11.0(否则被下面的
# docker-build 版本闸门拒掉)。
#
# 注意: 一旦跑过 vendor-src, docker-build 就**不能**再依赖 docker-fetch,
# 否则会被上游包覆盖回去(见下面 docker-build 的 guard)。
vendor-src:
	@sh scripts/build_vendor_from_source.sh

# 打镜像(先宿主预下载 Lume 二进制, 再 build; build 时 COPY vendor/ 离线安装)。
# 多架构: 见下方 docker-build-multiarch(它需 vendor/ 同时含两种 arch, 可用 LUME_VERSION 指定)。
#
# 若 vendor/.lume-build 存在(说明用的是本地源码构建), 跳过 docker-fetch ——
# 否则会把本地编译的带修复二进制换成上游 release 的, 静默退回旧行为。
docker-build:
	@if [ -f vendor/.lume-build ]; then \
	  echo "▶ vendor/ 来自本地源码构建(见 vendor/.lume-build), 跳过 docker-fetch"; \
	else \
	  $(MAKE) --no-print-directory docker-fetch; \
	fi
	@# lume-core/lib 在构建上下文之外(Docker 不允许 COPY ../), 先同步到 .runtime/
	@# 再由 Dockerfile COPY 进镜像根 /lume-core/lib —— 那是 lan.lume 里
	@# "../lume-core/lib/peer.lume" 从 /app 解析出来的位置。
	@if [ -d ../lume-core/lib ]; then \
	  rm -rf .runtime/lume-core && mkdir -p .runtime/lume-core && \
	  cp -R ../lume-core/lib .runtime/lume-core/ && \
	  echo "▶ 同步 lume-core/lib → .runtime/lume-core/lib"; \
	else \
	  echo "✗ 找不到 ../lume-core/lib (lume-core 仓库应与 lume-lan 同级)" >&2; exit 1; \
	fi
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
		docker run --rm -p 127.0.0.1:$(DOCKER_PORT):8091 -v $$(pwd)/.data:/app/.data:ro \
			-e ROUTER_PASS="$(ROUTER_PASS)" -e ROUTER_HOST="$(ROUTER_HOST)" $(DOCKER_IMAGE); \
	else \
		docker run --rm -p 127.0.0.1:$(DOCKER_PORT):8091 -v $$(pwd)/.data:/app/.data:ro \
			-e ROUTER_HOST="$(ROUTER_HOST)" $(DOCKER_IMAGE); \
	fi

# 后台起 / 停。主 docker-compose.yml 只含 lume-lan 看板(AdGuard 已迁宿主 launchd)。
# 宿主 web 端口: 看板 DOCKER_PORT(10091); AdGuard 控制台 ADGUARD_PORT(10095), 由宿主服务提供。
docker-up: check-peer-net
	@# 只有显式开了认证(KANBAN_AUTH 非空)才检查凭据文件。
	@# 默认关: 容器只绑 127.0.0.1, 认证的边际防护小, 而 Basic 的凭据缓存
	@# 不可靠 —— 表现为"每次刷新都要重新登录"。
	@if [ -n "$(KANBAN_AUTH)" ] && [ ! -f .runtime/htpasswd ]; then \
	  echo "✗ 开了 KANBAN_AUTH 但没有 .runtime/htpasswd —— 先 make auth-pass" >&2; \
	  exit 1; fi
	@echo "▶ 起看板容器 → http://127.0.0.1:$(DOCKER_PORT)/  (AdGuard 是宿主服务: make adguard-up)"
	@DOCKER_PORT=$(DOCKER_PORT) KANBAN_AUTH="$(KANBAN_AUTH)" docker compose up -d
	@echo "  (设备 DNS 首选指 <本机IP>:53 才有域名记录; 没配 AdGuard 也不影响看板)"

# ---- 跨应用共享网络(lume-peer-net) ----
#
# 各应用的 peer 节点**不发布宿主端口**(compose 里是 expose), 靠这张网按
# 别名互访: 看板 → crm-peer:18102 / scanner-peer:18103。
#
# 为什么用 external 而不是让某个 compose 定义: 三个应用各自独立部署,
# 谁先起都不该决定这张网的生命周期。代价是它可能不存在 —— 所以每个
# docker 相关目标都先过一遍这个检查, 而不是让人在报错里才第一次见到它。
#
# 幂等: 已存在就什么都不做(不 recreate, 免得把正在用的容器踢出网络)。
PEER_NET ?= lume-peer-net

check-peer-net:
	@if docker network inspect $(PEER_NET) >/dev/null 2>&1; then :; \
	 else \
	  echo "▶ 创建共享网络 $(PEER_NET)(各应用 peer 节点靠它按别名互访)"; \
	  docker network create $(PEER_NET) >/dev/null; \
	 fi

peer-net-rm:
	@docker network rm $(PEER_NET) 2>/dev/null && echo "  已删除 $(PEER_NET)" || echo "  $(PEER_NET) 不存在或仍被容器占用"

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

# compose 方式(主 compose 只含 lume-lan 看板; AdGuard 已迁宿主 launchd)。
# 端口经 env 传入(DOCKER_PORT + ADGUARD_PORT; compose 自动读 .env 做默认值替换)。
docker-compose:
	@DOCKER_PORT=$(DOCKER_PORT) ADGUARD_PORT=$(ADGUARD_PORT) docker compose up -d

docker-compose-down:
	@docker compose down

.PHONY: default dev lan lan-dev stop scan scan-sweep check test privacy hooks peer-serve peer-serve-agent peer-probe check-peer \
         peer-up peer-down peer-status peer-hint peer-register peer-registry peer-watch peer-watch-stop ui watch router-fetch notify-test daily-digest \
         adguard-up adguard-down adguard-log dns-fetch dns-watch peers-fetch peers-watch \
         a2a-worker a2a-worker-once \
         block block-list unblock \
         docker-fetch vendor-src docker-build docker-build-multiarch docker-run docker-up docker-down check-peer-net peer-net-rm docker-redeploy docker-compose docker-compose-down
