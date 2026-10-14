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
└──────────────────┬─────────────────────────────────┘
                   │ make scan（原子写快照）
┌─ Lume 服务端（lan.lume）──────────────────────────┐
│  server{ port 8091, docroot ./www, spa }          │
│  /api/devices   读 .data/devices.json + 派生统计   │
│  /api/meta      网关/网段/快照时间/是否过期        │
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
make scan         # 只刷数据源（arp 现成表，秒级）
make scan-sweep   # 加 ping sweep（LAN_SWEEP=1，捞出在线但 arp 表没的设备，较慢）
make check         # Lume --check lan.lume（DSL 语法）
make ui            # 只构建前端（esbuild → www/app.js）
make lan-dev       # check + 前端 watch + 起服务（改 .tsx 即刷）
```

## 安全

- 默认 `bind = 127.0.0.1`（只本机可访问）。容器化 / 局域网开放传
  `LUME_BIND=0.0.0.0`；公网务必注入 `HTPASSWD_FILE` 开 Basic Auth。
- 快照里是内网 IP/MAC，属隐私数据：`.data/` 已 gitignore，勿提交。

## 限制

- 数据是**快照**（`make scan` 的一次扫描结果），非实时流。浏览器「刷新」只重
  拉 Lume 已下发的快照；要新数据先宿主 `make scan`。
- `route get default` 在受限环境（如某些沙箱）会被 OS 拦，脚本兜底从
  `arp -a` 的 ifscope 条目取网关；仍拿不到才回落 `10.0.0.1`。
