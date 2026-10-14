# Lume LAN — 局域网设备看板

Lume（C 语言实现的 DSL 解释器）+ React 搭的一个局域网设备看板：宿主脚本扫
`arp`/ping 出当前内网设备快照，Lume 服务端把它做成 HTTP API，React 界面展示。

照 `lume-crm` 的范式（`.lume` 服务端 + esbuild 打 React 进 `www/` + Makefile 串联）搭的。

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
- **登录态（需密码）**：终端列表 + 每设备流量。HiLink 私有端点名不规律、请求体经
  `cat_enc.js` 加密，curl 直接猜不中。用法：`cp .env.example .env`，填 `ROUTER_PASS`；
  在浏览器登录后台看 Network 里「终端管理 / 流量」的真实请求，把端点填进
  `ROUTER_TERMINAL_API`（形如 `api/dhcp/dhcpclinet`），再 `make router-fetch`。
  取不到就优雅降级——不影响 arp 快照，界面只多一行提示。

## 安全

- 默认 `bind = 127.0.0.1`（只本机可访问）。容器化 / 局域网开放传
  `LUME_BIND=0.0.0.0`；公网务必注入 `HTPASSWD_FILE` 开 Basic Auth。
- 快照（`.data/`）是内网 IP/MAC，属隐私数据，已 gitignore，勿提交。
- 路由器凭据 `ROUTER_PASS` 放 `.env`（gitignore，不进仓）；路由器后台默认绑
  `127.0.0.1`，公网暴露前务必先开 Basic Auth（`HTPASSWD_FILE`）再考虑 `LUME_BIND`。

## 限制

- 数据是**快照**（`make scan` / `make router-fetch` 的一次抓取结果），非实时流。
  浏览器「刷新」只重拉 Lume 已下发的快照；要新数据先宿主重跑对应 make 目标。
- `route get default` 在受限环境（如某些沙箱）会被 OS 拦，脚本兜底从
  `arp -a` 的 ifscope 条目取网关；仍拿不到才回落 `10.0.0.1`。
- Lume 无 exec 内置：所有系统调用（arp/ping/curl 路由器）都在宿主脚本里做，
  Lume 侧只读 JSON 快照出 API。
