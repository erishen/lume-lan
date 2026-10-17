# Lume LAN — 局域网设备看板

[English](README.md) | [简体中文](README.zh.md)

Lume（C 语言实现的 DSL 解释器）+ React 搭的一个局域网设备看板：宿主脚本扫
`arp`/ping 出当前内网设备快照、抓路由器终端/流量、读 AdGuard DNS 记录，
Lume 服务端把它们做成 HTTP API，React 界面展示。

照 `lume-crm` 的范式（`.lume` 服务端 + esbuild 打 React 进 `www/` + Makefile 串联）搭的。

## 生态与血缘

> ⚠️ **`lume` 是自研的 C11 Web 应用引擎**，与 Deno 生态的 [lumeland/lume](https://github.com/lumeland/lume)（静态站生成器）**无关**，
> 也不是 [lume/lume](https://github.com/lume/lume)（CSS3D/WebGL 工具库）。撞名纯属巧合。

本仓库是 **lume 生态**的一员：

| 项目 | 是什么 | 链接 |
|---|---|---|
| **lume** | 完整框架：单二进制 C11 后端（`.lume` DSL + HTTP/SSE/SSR + SQLite + agent 工具） | https://github.com/erishen/lume |
| **lume-core** | 语言核心：词法/语法/类型检查 + 解释器 / LLVM 发射（host 无关） | https://github.com/erishen/lume-core |
| **lume-crm** | 用 lume 完整构建 CRM 后台的蓝本 | https://github.com/erishen/lume-crm |
| **lume-talentlens** | 读 GitHub 足迹出「人才信号」的招聘仪表盘 | https://github.com/erishen/lume-talentlens |
| **lume-nsgm** | AI 友好的确定性 CRUD 生成层（schema → 全栈 CRUD 工程） | https://github.com/erishen/lume-nsgm |
| **lume-lan（本仓库）** | 局域网设备 + DNS + 流量看板 | 待公开 |

**lume 与 lume-core 的区别**：**lume-core 是语言 + 核心运行时**——同一种 `.lume` 源码在解释器 / LLVM IR 文本 / libLLVM C API 三个后端上类型检查与执行结果一致，不依赖任何运行时；≥0.7.0 起内建 `server` / `route` / `run` / `sql_query` / `sql_write` / `docroot` 等运行时能力，单二进制即可跑起全栈应用。**lume 是完整框架**——在 lume-core 之上接入 agent-httpd（SSE 聊天 / MCP / 会话）与原生 SQLite，一个 ~1.7 MB 可执行文件就能 serve 静态站 + JSON API + 聊天 + agent。生态里的应用（crm / talentlens / nsgm / lan）都**构建在 lume-core 的语言与运行时之上**（用 `server` / `route` / `run` 等内建能力），需要 agent / SSE 聊天等增强时再叠加 lume 框架。

## 能展示什么

看板按 Tab 组织：**总览 / 设备 / 域名访问**（Tab 栏的设备/域名数字是实时计数）。

| 区 | 内容 | 数据源 |
|---|---|---|
| KPI | 设备总数 / 在线数（全网口径）/ 网关 / 快照 | arp + 路由器 |
| 路由器区 | 型号 / SN / OUI / 终端数 | 免密 `/api/system/deviceinfo` |
| 流量趋势曲线 | 家庭总流量随时间变化（每 60s 采样，近 135 分钟），**峰值时刻 + 主因设备**（谁在吃带宽） | `router_fetch.py` 累计流量历史差分 |
| 流量排行 Top5 | 累计下载 Top5，**时间窗可切换（近 1h / 近 5h / 累计）** + 活跃/挂起标签，点条目跳「设备」Tab | `HostInfo` + 流量历史 |
| 设备类型分布 | 手机 / 笔记本 / 电脑 / 电视 / 平板 / Android（**chip 可点击筛选**） | 路由器 `IconType` |
| Wi-Fi 频段分布 | 5GHz / 2.4GHz（**chip 可点击筛选**） | 路由器 `Frequency` |
| WAN 状态卡 | 连接态 / 接入方式 / IPv4 / 网关 / DNS / IPv6 / NAT 类型 / 带宽峰值 | 登录态 `ntwk/wan` |
| 域名访问 | 谁访问了什么域名（Top 列表 + 客户端分布），含 AdGuard 配置指引 | AdGuard Home `/control/querylog` |
| 完整终端列表 | 在线/挂起中/离线三态 + 活跃/挂起标签 + 设备备注（终端管理页改的名自动同步） | `HostInfo` |
| 设备表 | 本机 ARP + 路由器在线合并，设备名按 IP 关联，搜索框可搜 IP/设备名/MAC/网卡 | `arp -a` + `HostInfo` |
| 终端信号强度 | 每终端 3 段信号柱（好/中/差，按 RSSI），有线/未知显示 — | 登录态 `device/topology` |
| 异常流量告警 | 在线设备近 1h 增量 ≥`LAN_ANOMALY_GB`（默认 10GB）时顶部红条点名 | 流量历史 |
| 导出 CSV | 工具栏一键导出全部终端（名称/厂商/流量/信号/最后活跃）存档 | 前端 |

「设备类型」「Wi-Fi 频段」两行的 chip 是**筛选器**：点一下只看该类/该频段的设备，再点取消；
工具栏会显示当前激活的筛选（可一键清除）。

### 本地设备命名（`aliases.json`）

设备显示名的优先级：**本地别名 > 路由器备注(终端管理页改的, `ActualName`) > 设备自报名(HostName) > 厂商**。
前面两级是路由器登录态拉回来的；**本地别名不依赖登录**，适合「路由器里没改好名 / 登录态拉不到时也想自定义」的场景。

```
.data/aliases.json      # 本地命名层({ip: 名字}), 已 gitignore, 不入库
{ "192.168.1.100": "儿童故事机" }   # 示例: 换成你家真实设备 IP/名字
```

直接编辑这个文件 + 点界面「刷新」即可改名，不用等路由器同步。

## 架构

```
┌─ 宿主（Mac）────────────────────────────────────────────┐
│  launchd 60s 数据循环（com.<user>.lume-lan-refresh）     │
│   scripts/scan_lan.py        arp -a → .data/devices.json │
│   scripts/router_fetch.py    路由器登录态 → .data/router.json│
│   scripts/adguard_fetch.py   DNS 记录 → .data/dns*.json   │
│  （arp 扫描依赖宿主网卡，必须宿主跑；容器不跑）           │
└───────────────────┬──────────────────────────────────────┘
                    │ .data/ 快照挂载进容器（:ro 由容器读）
┌─ Docker 容器（lume-lan，端口 10091）─────────────────────┐
│  Lume server（lan.lume，绑 0.0.0.0:8091，docroot www）   │
│   /api/devices   /api/router   /api/dns …（读 .data 快照）│
│  Basic Auth：HTPASSWD_FILE → /auth/htpasswd（只读挂载）  │
└───────────────────┬──────────────────────────────────────┘
                    │ http://localhost:10091/（需登录）
┌─ 前端（src/lan/app.tsx → esbuild → www/app.js）──────────┐
│  总览/设备/域名访问 Tab + 时间窗 + 筛选 + 导出 + 告警      │
└──────────────────────────────────────────────────────────┘
```

服务端是**容器内静态 Lume 二进制**（产品版全静态，无运行时依赖）；前端是**仓库里已 build 的
`www/`**，容器内不跑 node/esbuild，只做「serve + 读快照 + 认证」。

## 快速上手（日常）

```bash
# 1. 确保数据在更新（launchd 已在跑，看 .runtime/refresh.log 有没有新轮次）
tail -3 .runtime/refresh.log

# 2. 起看板（首次需先 build 镜像）
make docker-up        # = docker compose up -d

# 3. 打开
#    http://localhost:10091/   （Basic Auth：admin / 见 make auth-pass 设置的密码）
```

数据采集由 **launchd 开机自启**（见下），不需要手动起循环；`make scan` / `make router-fetch`
可随时手动补一轮数据。

## 数据采集与自愈（launchd）

- 采集循环：`.runtime/refresh-once.sh` 每 60s 跑一轮
  （scan_lan → router_fetch → adguard_fetch），由 launchd 托管：
  `~/Library/LaunchAgents/com.<user>.lume-lan-refresh.plist`
  （`StartInterval=60` + `RunAtLoad`，开机即起，日志写 `.runtime/refresh.log`）。
- 注册（一次性）：`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.<user>.lume-lan-refresh.plist`
- 手动跑一轮：`bash .runtime/refresh-once.sh`
- 开发时替代：`make dev` 也会起 60s 后台循环（`make stop` 清理），两者互斥、不要同时开。

## 容器运行（Docker）

看板整个跑进容器：**产品版 `lume` 的全静态 Linux 二进制**（`erishen/lume` release 的
`lume-linux-<x64|arm64>-static`，无 glibc/libsqlite 运行时依赖，免装 libLLVM）+ 仓库里已 build
好的前端 `www/`。容器只做「serve + 读快照 + 认证」。

**分工**（关键）：
- **arp 扫描**（`scan_lan.py`）依赖宿主网卡，**只能在宿主跑**（launchd 循环负责）。
- **路由器抓取**（`router_fetch.py`，纯 stdlib）可容器内跑，也可宿主跑。compose 里 `REFRESH=0`
  表示容器只 serve、路由器数据靠宿主 launchd 写进 `.data/`；想容器内自刷新路由器，
  设 `REFRESH=1` + 打 `ROUTER_HOST`（arp 快照仍靠宿主）。

```bash
make docker-build                 # 打镜像(宿主预下载 Lume 二进制到 vendor/)
make docker-up                    # 后台起 → http://localhost:10091/ (downt: make docker-down)
make docker-redeploy              # 一键: 刷数据 + 重 build 前端 + 打镜像 + 重启容器
make docker-run DOCKER_PORT=11091 # 前台起 + 换端口(默认 10091, >10000 避开本地 dev 的 8091)
```

或用 `docker-compose.yml` 直接操作（`ROUTER_PASS` 走 env 注入，不落盘）：

```bash
export ROUTER_PASS='...'          # 供 compose 读, 不进镜像
docker compose up -d
```

- 多架构：`make docker-build-multiarch`（vendor/ 需同时含两种 arch）。
- 静态版全静态链接，仍基于 `python:3.12-slim`（容器内可选刷新循环要 python3），非 `scratch`。
- 网络：Mac 的 Docker Desktop 默认 NAT 通常能直达 `192.168.1.1`（示例网段，以你家为准）；连不通时改用 Linux host
  网络（compose 注释里有 `network_mode: host` 说明）。

## 看板认证（Basic Auth）

局域网内看板默认**开启 Basic Auth**（毕竟暴露了家里 IP/MAC/流量）：

```bash
make auth-pass        # 交互式设置 admin 密码 → 写 .runtime/htpasswd(bcrypt cost 12)
make docker-redeploy  # 重部署后生效(htpasswd 只读挂载进容器)
```

- 凭据文件 `.runtime/htpasswd`（600 权限，gitignore），compose 以 `:ro` 挂载到 `/auth/htpasswd`，
  容器内 Lume 通过 `HTPASSWD_FILE` 开启认证——**密码不进镜像、不进 git**。
- 改密码：重跑 `make auth-pass` + `make docker-redeploy`。

## AdGuard Home（域名访问数据源）

「域名访问」Tab 的数据来自 **AdGuard Home**，它已迁出容器、以 **Mac 宿主 launchd 服务**运行
（`com.<user>.adguardhome`，见 `~/Library/LaunchAgents/` + `~/Applications/AdGuardHome/`）。
原因：容器发布端口会把所有入站 DNS NAT 成同一个网关 IP，AdGuard 看不到真实设备；
宿主直绑 53 保留真实客户端 IP。

```bash
make adguard-up       # 启动宿主 AdGuard（launchd）
make adguard-down     # 停止
make adguard-log      # 看 AdGuard 日志
# 控制台: http://127.0.0.1:10095 (ADGUARD_PORT)
```

家里设备 DNS：「首选」指 `<Mac 本机 IP>:53`（AdGuard）、「备用」填 `223.5.5.5`。

## 路由器层（识别「设备使用情况」）

设备表默认只有 IP/MAC/网卡（本地 arp，认不出「是什么设备 / 谁在用」）。加一层
**路由器后台**（家用路由器，HiLink/CAT 协议，`http://<网关IP>/`，本仓库以 192.168.1.1 示例）：

- **免密（始终可用）**：`/api/system/deviceinfo` → 型号 / 序列号 / 厂商，界面「路由器」区直接显示。
- **登录态（需密码）**：终端列表 + 每设备流量 + 在线状态 + 最后活跃时间。
  `router_fetch.py` 自动完成 CAT 登录（SHA256 简单路径，独立于网页 SCRAM 的限流计数），
  然后读 `/api/system/HostInfo` 拉全部 DHCP 记录。
  用法：`cp .env.example .env`，填 `ROUTER_PASS`，再 `make router-fetch` 即可。
  取不到就优雅降级——不影响 arp 快照，界面只多一行提示。

## 依赖

- `lume` release：`~/.local/bin/lume`（安装 `curl -sSfL …/install.sh | sh`；
  或用 `../lume-core/bin/lume-core`）。
- `esbuild`：PATH 或 `../lume/frontend/node_modules/.bin/esbuild`（React 走
  本仓 `node_modules -> ../lume/frontend/node_modules` 软链，同 `lume-crm`）。
- 宿主扫描：`/usr/bin/python3` + 系统 `arp`（无第三方依赖）。

## 开发模式（改前端）

日常部署走容器；**改代码时**用 `make dev`（宿主直跑，端口 8091）：

```bash
make dev              # stop + check + scan + router-fetch + esbuild watch + Lume 前台
                      # → http://127.0.0.1:8091（无 Basic Auth，仅本机）
```

- `make ui`            一次性构建前端（esbuild → www/app.js，非 watch）
- `make check`         Lume --check lan.lume（DSL 语法）
- `make stop`          停 8091 旧服务 + 残留 esbuild watch + 后台刷新循环
- 前端改完 → `make ui` → `make docker-redeploy` 上到容器。

## 配置（.env）

`cp .env.example .env` 后按需填（`.env` gitignore，不进仓）：

| 变量 | 用途 |
|---|---|
| `ROUTER_HOST` / `ROUTER_USER` / `ROUTER_PASS` | 路由器后台地址与登录凭据 |
| `LAN_SWEEP=1` | arp 之外加 ping sweep（捞出在线但 arp 表没的设备，较慢） |
| `LAN_ANOMALY_GB=10` | 异常流量告警阈值（近 1h 增量 ≥ 该值点名） |
| `WEBHOOK_URL` / `WEBHOOK_SECRET` | 告警推送 webhook（不配则只打印日志） |
| `CRITICAL_IPS` | 关键设备 IP 列表（告警优先点名） |
| `ADGUARD_HOST` / `ADGUARD_PORT` | AdGuard 地址/端口（默认 127.0.0.1:10095） |

## 安全

- 看板默认 `HTPASSWD_FILE` 开 **Basic Auth**（`.runtime/htpasswd` 只读挂载，密码不进镜像/git）。
- 快照（`.data/`）是内网 IP/MAC，属隐私数据，已 gitignore，勿提交。
- 路由器凭据 `ROUTER_PASS` 放 `.env`（gitignore，不进仓）。容器若只 bind 127.0.0.1
  （`LUME_BIND`），仅本机可访问；开放到局域网/公网前务必先开 Basic Auth。

## 限制

- 数据是「最近一轮 launchd 循环」的准实时快照（默认每 60s）；点「刷新」重读最新落盘快照，
  慢接口（router-traffic 历史）异步到齐，「刷新中…」至少停留 1.2s 给出明确反馈。
- 流量趋势 / 平均速率 / 活跃-挂起判定都依赖**累积的历史**（`.data/router_traffic_hist*.json`），
  刚部署或历史被清后要等数据循环跑满约 10 分钟才有基线，期间增量按「未知」处理、不误判。
- 登录限流（`Too_Many_user`）后，`router_fetch.py` 有 5 分钟冷却：冷却/沿用旧数据时
  **不往流量历史写点**（避免趋势图出现假的"流量=0"平线），界面用「沿用上次的终端数据」提示。
- `route get default` 在受限环境（如某些沙箱）会被 OS 拦，脚本兜底从
  `arp -a` 的 ifscope 条目取网关；仍拿不到才回落 `10.0.0.1`。
- Lume 无 exec 内置：所有系统调用（arp/ping/登录路由器）都在宿主脚本里做，
  Lume 侧只读 JSON 快照出 API。
