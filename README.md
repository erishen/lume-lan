# Lume LAN — Local Network Device Dashboard

[English](README.md) | [简体中文](README.zh.md)

A home-LAN device dashboard built with **Lume** (a C11 DSL interpreter) + React: host
scripts sweep `arp`/ping for the current device snapshot, fetch router
terminal/traffic data, and read AdGuard DNS query logs; the Lume server exposes them
as HTTP APIs, rendered by the React frontend.

Built following the `lume-crm` pattern (`.lume` server + esbuild bundling React into
`www/` + Makefile orchestration).

## Ecosystem & Provenance

> ⚠️ **`lume` is a self-built C11 web engine** and is **unrelated** to
> [lumeland/lume](https://github.com/lumeland/lume) (the Deno static site generator),
> nor is it [lume/lume](https://github.com/lume/lume) (a CSS3D/WebGL toolkit).
> The name collision is coincidental.

This repository is a member of the **lume ecosystem**:

| Project | What it is | Link |
|---|---|---|
| **lume** | Full framework: single-binary C11 backend (`.lume` DSL + HTTP/SSE/SSR + SQLite + agent tools) | https://github.com/erishen/lume |
| **lume-core** | Language core: lexer/parser/type checker + interpreter / LLVM emitters (host-independent) | https://github.com/erishen/lume-core |
| **lume-crm** | A blueprint CRM admin built entirely with lume | https://github.com/erishen/lume-crm |
| **lume-talentlens** | Recruiting dashboard surfacing "talent signals" from GitHub footprints | https://github.com/erishen/lume-talentlens |
| **lume-nsgm** | AI-friendly deterministic CRUD generation layer (schema → full-stack CRUD project) | https://github.com/erishen/lume-nsgm |
| **lume-lan (this repo)** | LAN device + DNS + traffic dashboard | coming soon |

**lume vs lume-core**: **lume-core is the language + core runtime** — the same `.lume`
source type-checks and runs identically on all three backends (interpreter / LLVM IR
text / libLLVM C API), with no runtime dependency; since ≥0.7.0 it also ships built-in
`server` / `route` / `run` / `sql_query` / `sql_write` / `docroot` capabilities, so a
single binary can run a full-stack app. **lume is the full framework** — on top of
lume-core it adds agent-httpd (SSE chat / MCP / sessions) and native SQLite, letting a
~1.7 MB executable serve static sites + JSON APIs + chat + agents. Ecosystem apps
(crm / talentlens / nsgm / lan) all **build on lume-core's language and runtime**
(using built-in `server` / `route` / `run`), layering in the lume framework only when
agent / SSE chat enhancements are needed.

## What it shows

The dashboard is organized into tabs: **Overview / Devices / Domain Access** (the
device/domain counts in the tab bar update live).

| Section | Content | Data source |
|---|---|---|
| KPI | total devices / online (whole-network scope) / gateway / snapshot time | arp + router |
| Router card | model / SN / OUI / client count | password-free `/api/system/deviceinfo` |
| Traffic trend | household total traffic over time (sampled every 60s, last ~135 min), **peak moments + the device responsible** | `router_fetch.py` cumulative traffic diff |
| Traffic Top5 | cumulative download Top5, **switchable time window (1h / 5h / all-time)** + active/idle tags, click to jump to the Devices tab | `HostInfo` + traffic history |
| Device type breakdown | phone / laptop / computer / TV / tablet / Android (**clickable filter chips**) | router `IconType` |
| Wi-Fi band breakdown | 5GHz / 2.4GHz (**clickable filter chips**) | router `Frequency` |
| WAN status card | link state / access type / IPv4 / gateway / DNS / IPv6 / NAT type / bandwidth peak | authenticated `ntwk/wan` |
| Domain access | who accessed which domains (top list + per-client breakdown), with an AdGuard setup guide | AdGuard Home `/control/querylog` |
| Full client list | online / idle / offline states + active/idle tags + device notes (names edited in the router admin sync automatically) | `HostInfo` |
| Device table | local ARP merged with router-online devices, names resolved by IP, searchable by IP/name/MAC/NIC | `arp -a` + `HostInfo` |
| Signal strength | per-client 3-bar signal (good / medium / bad by RSSI); wired/unknown show — | authenticated `device/topology` |
| Anomaly alert | red banner calling out online devices whose last-1h delta ≥ `LAN_ANOMALY_GB` (default 10GB) | traffic history |
| Export CSV | one-click export of all clients (name / vendor / traffic / signal / last active) from the toolbar | frontend |

The chips in the "Device type" and "Wi-Fi band" rows are **filters**: click once to
show only that type/band, click again to clear; the toolbar shows the active filters
(with one-click reset).

### Local device naming (`aliases.json`)

Display-name precedence: **local alias > router note (edited in the client admin,
`ActualName`) > device self-reported name (`HostName`) > vendor**. The first two levels
come from the authenticated router session; **the local alias needs no login**, which
suits cases where the router name was never fixed or the authenticated session can't
be reached.

```
.data/aliases.json      # local naming layer ({ip: name}), gitignored, not committed
{ "192.168.1.100": "kids story player" }   # example: use your own device IP/name
```

Edit this file and click "Refresh" in the UI — no need to wait for the router sync.

## Architecture

```
┌─ Host (macOS) ───────────────────────────────────────────┐
│  launchd 60s refresh loop (com.<user>.lume-lan-refresh)   │
│   scripts/scan_lan.py        arp -a → .data/devices.json  │
│   scripts/router_fetch.py    router session → .data/router.json │
│   scripts/adguard_fetch.py   DNS queries → .data/dns*.json │
│  (arp scanning needs the host NIC — host only, not container) │
└───────────────────┬───────────────────────────────────────┘
                    │ .data/ snapshots mounted into the container (ro)
┌─ Docker container (lume-lan, port 10091) ─────────────────┐
│  Lume server (lan.lume, binds 0.0.0.0:8091, docroot www)  │
│   /api/devices  /api/router  /api/dns … (reads .data)     │
│  Basic Auth: HTPASSWD_FILE → /auth/htpasswd (ro mount)    │
└───────────────────┬───────────────────────────────────────┘
                    │ http://localhost:10091/ (login required)
┌─ Frontend (src/lan/app.tsx → esbuild → www/app.js) ───────┐
│  Overview / Devices / Domain tabs + time window + filters + export + alerts │
└───────────────────────────────────────────────────────────┘
```

The server is the **static Lume binary inside the container** (fully static product
build, no runtime deps); the frontend is the **pre-built `www/` in the repo** — the
container runs no node/esbuild, it only serves + reads snapshots + authenticates.

## Quick start (daily)

```bash
# 1. Make sure data is refreshing (launchd running; check .runtime/refresh.log for new rounds)
tail -3 .runtime/refresh.log

# 2. Start the dashboard (build the image first time only)
make docker-up        # = docker compose up -d

# 3. Open
#    http://localhost:10091/   (Basic Auth: admin / the password you set via make auth-pass)
```

Data collection is handled by **launchd at login** (see below); no need to start the
loop manually. `make scan` / `make router-fetch` can run an extra round any time.

## Data collection & self-healing (launchd)

- Refresh loop: `.runtime/refresh-once.sh` runs every 60s
  (scan_lan → router_fetch → adguard_fetch), managed by launchd:
  `~/Library/LaunchAgents/com.<user>.lume-lan-refresh.plist`
  (`StartInterval=60` + `RunAtLoad`, starts at login, logs to `.runtime/refresh.log`).
- Register (one-time):
  `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.<user>.lume-lan-refresh.plist`
- Run one round manually: `bash .runtime/refresh-once.sh`
- Dev alternative: `make dev` also starts a 60s background loop (`make stop` cleans it
  up); the two are mutually exclusive — don't run both.

## Running in Docker

The whole dashboard runs in a container: the **fully static Linux binary of the
product `lume`** (the `lume-linux-<x64|arm64>-static` asset from the `erishen/lume`
releases — no glibc/libsqlite runtime deps, no libLLVM install) + the **pre-built
frontend `www/`** in the repo. The container only serves + reads snapshots +
authenticates.

**Division of labor** (key):
- **arp scanning** (`scan_lan.py`) needs the host NIC, so it **runs on the host only**
  (the launchd loop handles it).
- **Router fetching** (`router_fetch.py`, pure stdlib) can run in the container or on
  the host. `REFRESH=0` in compose means the container only serves and router data is
  written into `.data/` by the host launchd loop; for in-container router refresh set
  `REFRESH=1` + `ROUTER_HOST` (arp snapshots still come from the host).

```bash
make docker-build                 # build image (host pre-downloads the Lume binary to vendor/)
make docker-up                    # start in background → http://localhost:10091/ (down: make docker-down)
make docker-redeploy              # one-shot: refresh data + rebuild frontend + build image + restart container
make docker-run DOCKER_PORT=11091 # foreground + custom port (default 10091, >10000 to avoid local dev 8091)
```

Or use `docker-compose.yml` directly (`ROUTER_PASS` injected via env, never persisted):

```bash
export ROUTER_PASS='...'          # read by compose, not baked into the image
docker compose up -d
```

- Multi-arch: `make docker-build-multiarch` (vendor/ must contain both arches).
- The static build is fully static but still bases on `python:3.12-slim` (the optional
  in-container refresh loop needs python3), not `scratch`.
- Networking: Mac's Docker Desktop default NAT usually reaches `192.168.1.1` (example
  subnet — use yours); if it can't, switch to Linux host networking (see the
  `network_mode: host` note in the compose comments).

## Dashboard authentication (Basic Auth)

The dashboard enables **Basic Auth by default** (it does expose your home
IP/MAC/traffic):

```bash
make auth-pass        # interactive: set the admin password → writes .runtime/htpasswd (bcrypt cost 12)
make docker-redeploy  # redeploy to apply (htpasswd mounted ro into the container)
```

- The credential file `.runtime/htpasswd` (0600, gitignored) is mounted `:ro` to
  `/auth/htpasswd`; the in-container Lume enables auth via `HTPASSWD_FILE` —
  **the password never enters the image or git**.
- Change password: re-run `make auth-pass` + `make docker-redeploy`.

## AdGuard Home (domain-access data source)

The "Domain Access" tab reads from **AdGuard Home**, which has been moved out of the
container and runs as a **macOS host launchd service**
(`com.<user>.adguardhome`; see `~/Library/LaunchAgents/` + `~/Applications/AdGuardHome/`).
Why: publishing the container port NATs all inbound DNS to a single gateway IP, so
AdGuard can't see real devices; binding 53 on the host preserves real client IPs.

```bash
make adguard-up       # start host AdGuard (launchd)
make adguard-down     # stop
make adguard-log      # follow AdGuard logs
# Console: http://127.0.0.1:10095 (ADGUARD_PORT)
```

Point your home devices' DNS: primary = `<Mac's LAN IP>:53` (AdGuard), secondary =
`223.5.5.5`.

## Router layer (identifying device usage)

The device table alone only has IP/MAC/NIC (local arp — it can't tell "what device /
who's using it"). Add a **router admin** layer (consumer router, HiLink/CAT protocol,
`http://<gateway-ip>/`, this repo uses 192.168.1.1 as an example):

- **Password-free (always available)**: `/api/system/deviceinfo` → model / serial /
  vendor, shown directly in the "Router" card.
- **Authenticated (needs password)**: client list + per-device traffic + online state +
  last-active time. `router_fetch.py` automates the CAT login (a SHA256 simple path,
  separate from the web SCRAM rate-limit counter), then reads `/api/system/HostInfo`
  for all DHCP records.
  Usage: `cp .env.example .env`, fill in `ROUTER_PASS`, then `make router-fetch`.
  If it can't be reached it degrades gracefully — the arp snapshot is unaffected, the
  UI just shows one extra hint line.

## Dependencies

- `lume` release: `~/.local/bin/lume` (install `curl -sSfL …/install.sh | sh`;
  or use `../lume-core/bin/lume-core`).
- `esbuild`: on PATH or `../lume/frontend/node_modules/.bin/esbuild` (React via this
  repo's `node_modules -> ../lume/frontend/node_modules` symlink, same as `lume-crm`).
- Host scanning: `/usr/bin/python3` + system `arp` (no third-party deps).

## Dev mode (frontend changes)

Daily deployment runs in the container; **when changing code** use `make dev` (runs on
the host, port 8091):

```bash
make dev              # stop + check + scan + router-fetch + esbuild watch + Lume foreground
                      # → http://127.0.0.1:8091 (no Basic Auth, localhost only)
```

- `make ui`            one-shot frontend build (esbuild → www/app.js, not watch)
- `make check`         Lume --check lan.lume (DSL syntax)
- `make stop`          stop the 8091 service + leftover esbuild watch + background refresh loop
- After frontend changes → `make ui` → `make docker-redeploy` to ship into the container.

## Configuration (.env)

`cp .env.example .env` then fill in what you need (`.env` is gitignored):

| Variable | Purpose |
|---|---|
| `ROUTER_HOST` / `ROUTER_USER` / `ROUTER_PASS` | router admin address and credentials |
| `LAN_SWEEP=1` | add a ping sweep on top of arp (catches online-but-not-in-arp devices; slower) |
| `LAN_ANOMALY_GB=10` | anomaly traffic alert threshold (last-1h delta ≥ this value gets called out) |
| `WEBHOOK_URL` / `WEBHOOK_SECRET` | alert push webhook (unset → log only) |
| `CRITICAL_IPS` | critical device IP list (alerted first) |
| `ADGUARD_HOST` / `ADGUARD_PORT` | AdGuard address/port (default 127.0.0.1:10095) |

## Security

- The dashboard enables **Basic Auth** by default via `HTPASSWD_FILE`
  (`.runtime/htpasswd` mounted ro; password never in image/git).
- Snapshots (`.data/`) contain LAN IPs/MACs — privacy-sensitive; gitignored, never
  commit them.
- Router credentials `ROUTER_PASS` live in `.env` (gitignored). If the container binds
  only 127.0.0.1 (`LUME_BIND`), only localhost can reach it; before exposing to the
  LAN/internet, enable Basic Auth first.

## Limitations

- Data is a near-real-time snapshot of the **latest launchd round** (every 60s by
  default); "Refresh" re-reads the latest on-disk snapshot; slow endpoints
  (router-traffic history) resolve asynchronously, and "Refreshing…" stays at least
  1.2s to give clear feedback.
- Traffic trend / average rate / active-idle classification all depend on
  **accumulated history** (`.data/router_traffic_hist*.json`); right after deployment
  or after the history is cleared, wait ~10 minutes for the loop to build a baseline —
  deltas are treated as "unknown" until then, no false alerts.
- After login rate-limit (`Too_Many_user`), `router_fetch.py` cools down for 5 minutes:
  during cooldown/stale-data reuse it **doesn't write points into the traffic history**
  (avoiding fake flat "traffic=0" lines), and the UI shows a "reusing last client data"
  hint.
- `route get default` is blocked by the OS in restricted environments (e.g. some
  sandboxes); the script falls back to parsing the ifscope entries of `arp -a` for the
  gateway; if that fails too it falls back to `10.0.0.1`.
- Lume has no exec built-in: all system calls (arp/ping/router login) happen in host
  scripts; the Lume side only reads JSON snapshots and serves APIs.
