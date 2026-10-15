# Lume LAN — 局域网设备看板

Lume（C 语言实现的 DSL 解释器）+ React 搭的一个局域网设备看板：宿主脚本扫
`arp`/ping 出当前内网设备快照，Lume 服务端把它做成 HTTP API，React 界面展示。

照 `lume-crm` 的范式（`.lume` 服务端 + esbuild 打 React 进 `www/` + Makefile 串联）搭的。

## 能展示什么

| 区 | 内容 | 数据源 |
|---|---|---|
| KPI | 设备总数 / 在线数（全网口径）/ 网关 / 快照 | arp + 路由器 |
| 路由器区 | 型号 / SN / OUI | 免密 `/api/system/deviceinfo` |
| 流量趋势曲线 | 家庭总流量随时间变化（每 60s 采样，300 点 ≈ 5h），**峰值时刻 + 主因设备**（谁在吃带宽） | `router_fetch.py` 累计流量历史差分 |
| 设备类型分布 | 手机 / 笔记本 / 电脑 / 电视 / 平板 / Android（**chip 可点击筛选**） | 路由器 `IconType` |
| Wi-Fi 频段分布 | 5GHz / 2.4GHz（**chip 可点击筛选**） | 路由器 `Frequency` |
| 流量排行 Top5 | 累计下载 Top5 条形图 + 近 1h 平均速率 + 活跃/挂起标签 | `HostInfo` + 流量历史 |
| 完整终端列表 | 在线/挂起中/离线三态 + 活跃/挂起标签 + 你的设备备注（终端管理页改的名自动同步） | `HostInfo` |
| 设备表 | 本机 ARP + 路由器在线合并，设备名按 IP 关联，搜索框可搜 IP/设备名/MAC/网卡 | `arp -a` + `HostInfo` |

「设备类型」「Wi-Fi 频段」两行的 chip 现在是**筛选器**：点一下只看该类/该频段的设备，再点取消；工具栏会显示当前激活的筛选（可一键清除）。

### 本地设备命名（`aliases.json`）

设备显示名的优先级：**本地别名 > 路由器备注(终端管理页改的, `ActualName`) > 设备自报名(HostName) > 厂商**。
前面两级是路由器登录态拉回来的；**本地别名不依赖登录**，适合「路由器里没改好名 / 登录态拉不到时也想自定义」的场景。

```
.data/aliases.json      # 本地命名层({ip: 名字}), 已 gitignore, 不入库
{ "192.168.3.36": "米兔故事机" }
```

直接编辑这个文件 + 点界面「刷新」即可改名，不用等路由器同步。

## 架构

```
┌─ 宿主（Mac / Linux）──────────────────────────────┐
│  scripts/scan_lan.py                               │
│   arp -a（秒级）或 LAN_SWEEP=1 ping sweep（较慢）  │
│   → 写 .data/devices.json（网关/网段/设备列表）     │
│                                                    │
│  scripts/router_fetch.py（make router-fetch）     │
│   打路由器后台(华为 WS5102/HiLink) http://192.168.3.1/ │
│   免密 /api/system/deviceinfo → 路由器身份(型号/序列号)│
│   终端/流量(登录态,需 .env ROUTER_PASS)→ .data/router.json │
└──────────────────┬─────────────────────────────────┘
                   │ make scan（原子写快照）
┌─ Lume 服务端（lan.lume）──────────────────────────┐
│  server{ port 8091, docroot ./www, spa }          │
│  /api/devices   读 .data/devices.json + 派生统计   │
│  /api/meta      网关/网段/快照时间/是否过期        │
│  /api/router    读 .data/router.json（路由器身份+终端）│
│  （lume-core 无 exec/子进程内置，系统调用放宿主）   │
└──────────────────┬─────────────────────────────────┘
                   │ Lume 直服 www/（React SPA 经它挂）
┌─ 前端（src/lan/app.tsx → esbuild → www/app.js）───┐
│  仪表盘：KPI（设备/在线/网关/快照）+ 设备表 + 搜索  │
│  + 只看在线筛选 + 刷新（重拉 /api/devices）         │
└────────────────────────────────────────────────────┘
```

## 依赖

- `lume` release：`~/.local/bin/lume`（安装 `curl -sSfL …/install.sh | sh`；
  或用 `../lume-core/bin/lume-core`）。
- `esbuild`：PATH 或 `../lume/frontend/node_modules/.bin/esbuild`（React 走
  本仓 `node_modules -> ../lume/frontend/node_modules` 软链，同 `lume-crm`）。
- 宿主扫描：`/usr/bin/python3` + 系统 `arp`（无第三方依赖）。

## 跑起来

```
make lan          # check + 构建前端 + 首跑扫描 + 起服务
                  # → http://127.0.0.1:8091
```

分步：

```
make scan         # 只刷设备数据源（arp 现成表，秒级）
make scan-sweep   # 加 ping sweep（LAN_SWEEP=1，捞出在线但 arp 表没的设备，较慢）
make router-fetch # 抓路由器后台（华为 WS5102/HiLink）→ 路由器身份 + 终端/流量
make stop         # 只停：kill 8091 旧服务 + 残留 esbuild watch（不重启）
make check         # Lume --check lan.lume（DSL 语法）
make ui            # 一次性构建前端（esbuild → www/app.js，非 watch）
```

日常就 `make dev`：它会 `stop` 清掉上一次占端口的实例 → check → scan → router-fetch
→ 起 esbuild `--watch` + Lume（前台，`Ctrl-C` 退出时自动清 watch），`.tsx` 改动秒级热刷。

## 路由器层（识别「设备使用情况」）

设备表默认只有 IP/MAC/网卡（本地 arp，认不出「是什么设备 / 谁在用」）。加一层
**路由器后台**（你的网关是华为 WS5102，HiLink/CAT 协议，`http://192.168.3.1/`）：

- **免密（始终可用）**：`/api/system/deviceinfo` → 型号 / 序列号 / 厂商，界面「路由器」区直接显示。
- **登录态（需密码）**：终端列表 + 每设备流量 + 在线状态 + 最后活跃时间。
  `router_fetch.py` 自动完成 CAT 登录（SHA256 简单路径，独立于网页 SCRAM 的限流计数），
  然后读 `/api/system/HostInfo` 拉全部 DHCP 记录。
  用法：`cp .env.example .env`，填 `ROUTER_PASS`，再 `make router-fetch` 即可。
  取不到就优雅降级——不影响 arp 快照，界面只多一行提示。

## 安全

- 默认 `bind = 127.0.0.1`（只本机可访问）。容器化 / 局域网开放传
  `LUME_BIND=0.0.0.0`；公网务必注入 `HTPASSWD_FILE` 开 Basic Auth。
- 快照（`.data/`）是内网 IP/MAC，属隐私数据，已 gitignore，勿提交。
- 路由器凭据 `ROUTER_PASS` 放 `.env`（gitignore，不进仓）；路由器后台默认绑
  `127.0.0.1`，公网暴露前务必先开 Basic Auth（`HTPASSWD_FILE`）再考虑 `LUME_BIND`。

## 限制

- `make dev` 会起一个**后台数据刷新循环**（默认每 `REFRESH_SECS=60` 秒重跑
  `scan` + `router-fetch`，写 `.runtime/refresh.pid`，`make stop` 清理），所以日常开发时
  数据是「近一次循环」的准实时快照。只起 Lume（不跑 `make dev`）时，刷新按钮只重读上次落盘的快照。
- 流量趋势 / 平均速率 / 活跃-挂起判定都依赖**累积的历史**（`.data/router_traffic_hist*.json`），
  刚部署或历史被清后要等数据循环跑满 10 分钟才有基线，期间增量按「未知」处理、不误判。
- 登录限流（`Too_Many_user`）后，`router_fetch.py` 有 5 分钟冷却：冷却/沿用旧数据时
  **不往流量历史写点**（避免趋势图出现假的"流量=0"平线），界面用「沿用上次的终端数据」提示。
- `route get default` 在受限环境（如某些沙箱）会被 OS 拦，脚本兜底从
  `arp -a` 的 ifscope 条目取网关；仍拿不到才回落 `10.0.0.1`。
- Lume 无 exec 内置：所有系统调用（arp/ping/登录路由器）都在宿主脚本里做，
  Lume 侧只读 JSON 快照出 API。
