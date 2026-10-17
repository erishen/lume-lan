# lume-lan 架构文档

> 局域网设备看板（基于自研 lume 引擎）的系统架构说明。
> 适用版本：本仓库当前 main 分支。

## 1. 总览

lume-lan 是一个**「宿主采集 + 容器伺服 + 前端展示」三层架构**的家庭局域网看板：

```
┌─────────────────────────────────────────────────────────────┐
│ 宿主 macOS（采集层）                                          │
│   launchd 60s 循环                                            │
│   scan_lan.py      arp/ping → .data/devices.json             │
│   router_fetch.py  路由器登录态 → .data/router*.json         │
│   adguard_fetch.py AdGuard DNS → .data/dns.json              │
└──────────────┬──────────────────────────────────────────────┘
               │ .data/ 快照（宿主私有，0600，gitignore）
┌──────────────▼──────────────────────────────────────────────┐
│ Docker 容器 lume-lan（伺服层，端口 10091）                   │
│   Lume server（lan.lume，绑 0.0.0.0:8091）                   │
│   只读挂载 .data/ + /auth/htpasswd                           │
│   GET /api/devices | /api/router | /api/dns | ...            │
└──────────────┬──────────────────────────────────────────────┘
               │ HTTP + Basic Auth
┌──────────────▼──────────────────────────────────────────────┐
│ 浏览器前端（展示层）                                          │
│   src/lan/app.tsx → esbuild → www/app.js（容器内静态托管）    │
│   总览 / 设备 / 域名访问 Tab + 时间窗 + 筛选 + 导出 + 告警     │
└─────────────────────────────────────────────────────────────┘
```

核心思路：**谁有权限谁采集**——arp 扫描依赖宿主网卡、路由器抓取需要真实网段、DNS 记录来自宿主 AdGuard，全部在宿主完成并落盘成 JSON 快照；容器只做「读快照 + 出 API + 认证」，保持镜像纯净、凭据最小化。

## 2. 部署拓扑

| 组件 | 位置 | 说明 |
|---|---|---|
| launchd 服务 `com.<user>.lume-lan-refresh` | 宿主 macOS | `StartInterval=60` + `RunAtLoad`，开机自启、崩溃自动拉起；跑 `.runtime/refresh-once.sh` |
| 采集脚本（3 个） | 宿主 `scripts/` | 纯 stdlib Python，零第三方依赖 |
| AdGuard Home | 宿主（`~/Applications/AdGuardHome/`） | launchd 服务 `com.<user>.adguardhome`，直绑 `0.0.0.0:53`；控制台 `127.0.0.1:10095` |
| lume-lan 容器 | Docker（10091） | 静态 Lume 二进制 + 预构建前端；只读挂载数据与凭据 |
| 前端静态资源 | 容器内 `www/` | 仓库已 build，容器内不跑 node/esbuild |
| 告警推送（可选） | 宿主脚本 → 钉钉/企微 | `WEBHOOK_URL`/`WEBHOOK_SECRET` 配置后推送异常流量与日报 |

## 3. 数据流（60s 周期）

```
t0          launchd 触发 refresh-once.sh
t0+0s       scan_lan.py        arp -a（可选 ping sweep）→ 写 .data/devices.json
t0+~1s      router_fetch.py    CAT 登录（限流冷却跳过时沿用旧数据）
            ├─ 免密 /api/system/deviceinfo → 型号/SN/厂商
            ├─ 登录态 /api/system/HostInfo → 终端/流量/在线状态
            └─ 流量差分 → .data/router_traffic_hist*.json（冷却期不写点）
t0+~2s      adguard_fetch.py   AdGuard /control/querylog → .data/dns.json
t0+~3s      本轮结束，快照落盘
...         容器 /api/* 读最新落盘快照；浏览器点「刷新」触发重读
t0+60s      下一轮（launchd 保持调度）
```

**关键**：数据是**准实时快照**而非流式——每轮全量重写 JSON，读侧（容器/前端）永远拿到「最新一轮」的完整状态，无状态、无共享内存、无跨进程耦合。

## 4. 模块详解

### 4.1 宿主采集层

| 脚本 | 职责 | 输出 |
|---|---|---|
| `scan_lan.py` | 读系统 `arp -a`（可选 `LAN_SWEEP=1` 加 ping 探测）→ 在线设备快照 | `.data/devices.json` |
| `router_fetch.py` | 家用路由器（HiLink/CAT 协议）双路径：免密 deviceinfo + 登录态 HostInfo；累计流量差分；异常流量告警状态机；每日日报触发 | `.data/router.json`、`.data/router_traffic_hist*.json`、`.data/alert_state.json`、`.data/digest_state.json` |
| `adguard_fetch.py` | 拉 AdGuard querylog → Top 域名 + 客户端分布（近 1h） | `.data/dns.json` |
| `webhook_notify.py` | 零依赖 webhook 推送（钉钉加签 / 企微 / Server酱） | 告警/日报消息 |
| `daily_digest.py` | 24h 周期的日报汇总（在线台数、增量流量 Top5、WAN、告警数） | webhook 推送 |

**优雅降级**：路由器取不到 → 界面多一行提示、arp 快照不受影响；AdGuard 未配置 → dns 数据为空、Tab 显示配置指引；任何单脚本失败不影响其他脚本与看板运行。

### 4.2 数据层（`.data/`，宿主私有，0600）

| 文件 | 内容 | 生产方 |
|---|---|---|
| `devices.json` | 本机 ARP 快照（含网关/子网推导） | scan_lan |
| `router.json` | 路由器型号/SN/厂商 + 登录态终端列表（名称/流量/在线/活跃） | router_fetch |
| `router_traffic_hist.json` | 总流量历史（趋势图基线） | router_fetch |
| `router_traffic_hist_total.json` | 累计流量历史（Top5 时间窗/排行） | router_fetch |
| `router_traffic_payload.json` | 终端列表 + 每设备流量（前端接口预聚合） | router_fetch |
| `dns.json` | 近 1h DNS 查询（Top 域名 + 客户端分布） | adguard_fetch |
| `aliases.json` | 本地设备命名层 `{ip: 名字}`（最高优先级显示名） | 手工编辑 |
| `alert_state.json` | 告警去重状态（anomaly_ts、known_macs、wan_status） | router_fetch |
| `digest_state.json` | 日报节奏状态（距上次 ≥24h 才推） | router_fetch |
| `adguard/` | AdGuard 相关派生数据 | adguard_fetch |

### 4.3 服务端（Lume server，`lan.lume`）

用 lume-core 的 `server{}` + `get` 路由（≥0.7.0 内建运行时能力）：

| 端点 | 数据源 | 用途 |
|---|---|---|
| `GET /api/devices` | `.data/devices.json` | 设备表 / KPI / 设备 Tab |
| `GET /api/meta` | 推导 + 快照元信息 | 网关/子网/快照时间等 |
| `GET /api/router` | `.data/router.json` | 路由器卡 / 终端列表 / 类型与频段分布 |
| `GET /api/router-traffic` | `.data/router_traffic_payload.json` + 历史 | 流量趋势 / 排行 Top5 / 单设备趋势 |
| `GET /api/dns` | `.data/dns.json` | 域名访问 Tab |
| `GET /`（docroot `./www`） | 静态前端 | 看板入口（Basic Auth 保护） |

服务端**无 exec 能力**：不发起任何系统调用，只读 JSON 出 API——安全面收敛到「读文件 + 响应」两层。

### 4.4 认证层（Basic Auth）

- 凭据：`.runtime/htpasswd`（bcrypt cost 12，0600，gitignore），compose 以 `:ro` 挂载到容器 `/auth/htpasswd`
- 开关：`lan.lume` 读 `HTPASSWD_FILE` 环境变量——有则开认证，无则放行（本地 `make dev` 场景）
- **密码不进镜像、不进 git、不进容器环境变量**（文件只读挂载）

### 4.5 前端（React，`src/lan/`）

```
src/lan/app.tsx   看板主应用：Tab 组织（总览/设备/域名访问）+ 时间窗切换 + 筛选 chips + 导出 + 告警条
src/lan/sites.ts  域名知识库：三态分类（site=可点开 / cdn=资源域 / sdk=遥测推送），决定链接行为
src/api.ts        前端 API 封装（fetch /api/*）
src/devices.lume  设备数据推导（Lume 侧逻辑，供服务端复用）
```

前端经 esbuild 打包为单文件 `www/app.js`（无框架运行时外依赖），容器内静态托管。时间窗（近 1h / 近 5h / 累计）与设备类型/频段筛选都在**前端本地聚合**，不改服务端接口。

### 4.6 AdGuard 宿主服务

「域名访问」数据源。**已迁出容器、以宿主 launchd 运行**，原因：

- 容器发布端口会把所有入站 DNS NAT 成同一个网关 IP → AdGuard 看到的全是同一客户端，无法区分设备
- 宿主直绑 53 保留真实客户端 IP → 看板能展示「谁访问了什么域名」

配置要点：querylog 保留 **7d**（数据留存在本地、不外发）；控制台仅绑定 `127.0.0.1:10095`。

### 4.7 告警与日报（可选外发）

- **异常流量告警**：在线设备近 1h 增量 ≥ `LAN_ANOMALY_GB`（默认 10GB）→ 顶部红条点名 +（配置了 webhook 时）推送到钉钉/企微；30 分钟去重
- **每日日报**：距上次 ≥24h 推一条汇总（在线台数 / 增量流量 Top5 / WAN 状态 / 告警数）
- 推送内容为汇总文本（含设备名/IP，**不含 DNS 明细**）；未配置 webhook 则只写日志

## 5. 关键设计决策

| 决策 | 原因 |
|---|---|
| **arp 扫描只在宿主跑** | 依赖宿主网卡；容器内无宿主网络栈 |
| **AdGuard 迁出容器** | 容器发布端口的 NAT 抹掉客户端 IP，看板无法区分设备 |
| **容器默认只 serve（`REFRESH=0`）** | 凭据最小化：路由器密码/webhook 密钥不进入容器环境，容器被攻破也拿不到 |
| **`.data/` 只读挂载（`:ro`）** | 容器只读快照出 API，被攻破也改不了/删不了宿主数据 |
| **htpasswd 文件挂载而非注入** | 密码不进镜像、不进环境变量 |
| **数据全部本地 JSON 快照** | 无数据库依赖、无状态服务、重启自愈；隐私数据不出宿主机 |
| **launchd 托管采集** | 开机自启 + 崩溃自动拉起 + 日志落盘，无需常驻守护进程 |
| **静态 Lume 二进制 + 预构建前端** | 镜像无编译依赖（免 node/libLLVM），构建快、体积小 |
| **域名链接三态分类（sites.ts）** | 遥测/推送/统计端点（jpush/bugly/sentry 等）不加链接，避免打开死链 |

## 6. 目录结构

```
lume-lan/
├── lan.lume              服务端入口（server{} + 路由）
├── src/
│   ├── api.ts            前端 API 封装
│   ├── devices.lume      Lume 侧设备推导逻辑
│   └── lan/
│       ├── app.tsx       看板主应用
│       └── sites.ts      域名知识库（三态分类）
├── scripts/              宿主采集与推送脚本（纯 stdlib）
├── www/                  预构建前端 bundle（esbuild 产物）
├── vendor/               预下载的 Lume 静态二进制（构建时离线安装）
├── docs/                 文档（本架构文档、全貌导图）
├── Dockerfile            容器镜像（多架构 buildx）
├── docker-compose.yml    编排（只读挂载 + 凭据最小化）
├── Makefile              命令入口（dev/docker/auth/adguard/…）
├── .data/                [gitignore] 宿主快照（0600）
├── .runtime/             [gitignore] 运行态（htpasswd、refresh.log、pid）
└── .env                  [gitignore] 本机凭据与配置
```

## 7. 安全模型

- **凭据最小化**：`.env`（0600、gitignore）只在宿主侧被脚本读取；容器环境变量凭据数 = 0（默认模式）；htpasswd 文件只读挂载
- **数据本地化**：DNS 记录（7d）、设备快照全部留在宿主机 `.data/`（0600），不进镜像、不进 git
- **访问控制**：看板默认 Basic Auth；AdGuard 控制台仅 loopback；DNS 端口 53 对局域网开放（功能必需）
- **边界**：10091 绑 `0.0.0.0`（局域网可达、Basic Auth 保护）——如开放公网需先经 VPN 或更强认证；webhook 推送含设备名/IP（家庭自用范围），如在意可配 `aliases.json` 别名

## 8. 限制与边界

- 准实时快照：数据是「最近一轮循环」的状态（默认 60s），非流式
- 流量基线依赖累积历史：部署初期约 10 分钟无基线，增量按「未知」处理
- 路由器登录限流后有 5 分钟冷却，冷却期不写流量历史点（避免趋势图假平线）
- `route get default` 在受限环境被 OS 拦截时，脚本兜底从 `arp -a` ifscope 解析网关
- 单机部署设计：无多实例、无横向扩展；适配家庭网络单路由器场景
