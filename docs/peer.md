# 跨机 peer 通讯

> **模块位置**：peer 协议已下沉到 **`lume-core/lib/peer.lume`**（lume 生态共用），
> 本仓库只保留应用侧的节点服务与验活脚本：
> `peer_node.lume` / `peer_probe.lume`，通过
> `import "../lume-core/lib/peer.lume" as peer;` 引入。
>
> 下沉原因：peer 不依赖 lume-lan 的任何业务（设备/DNS/router），只依赖
> lume-core 的 `http_*`、`hmac_sha256`、`server{}`，属于语言层网络能力的补全，
> 放在应用里会让每个 lume 项目各抄一份。

两台机器上的 lume agent 互相调用。A 可以探测 B、拉取 B 的能力清单、把提问转发给 B；
B 同样可以反向调用 A（对称，双方各跑同一个 `peer_node.lume`）。

## 拓扑

```
机器 A (192.168.x.a)                  机器 B (192.168.x.b)
┌──────────────────────────┐          ┌──────────────────────────┐
│ peer_node.lume :18092    │◄────────►│ peer_node.lume :18092    │
│  /peer/health            │  局域网   │  /peer/health            │
│  /peer/tools             │  明文HTTP │  /peer/tools             │
│  /peer/ask               │  +HMAC   │  /peer/ask               │
│                          │          │                          │
│  peer_probe.lume ────────┼──调用───►│                          │
└──────────────────────────┘          └──────────────────────────┘
```

## 快速开始

两台机器上都放好仓库，各写一份 `.env`（`PEER_SHARED` 必须**完全相同**）：

```bash
# 只在其中一台生成一次，然后复制到另一台
LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32
```

单机先跑通（最常见的第一步）：

```bash
cp .env.example .env    # 按需改 PEER_SHARED / PEER_TARGETS
make peer-up            # agent-httpd(:18099) + peer_node(:18092)
make peer-status
```

跨机再按下一节「跨机部署清单」逐项对齐。要手工起单个进程（调试用）：

机器 B：

```bash
PEER_SHARED=<同一个密钥> PEER_NAME=nodeB PEER_BIND=0.0.0.0 PEER_PORT=18092 \
  ../lume/bin/lume peer_node.lume
```

机器 A 验活：

```bash
PEER_SHARED=<同一个密钥> PEER_NAME=nodeA PEER_HOST=192.168.x.b:18092 \
  ../lume/bin/lume peer_probe.lume
```

`peer_probe.lume` 会跑 7 项检查，全过则 exit 0，否则 exit 为失败数。适合直接当
部署后的验活命令，或塞进 launchd / CI。

> ⚠️ **用仓库里的 lume，别用 `~/.local/bin/lume`。**
> 后者是某个旧版本（本机是 10-23 构建），与工作区源码不一致：新语法/新类型
> 规则它不认识，`--check` 会漏报；两个版本同时常驻还会争同一端口与同一份
> db，数据时而正常时而全 0，极难排查。
> 正确写法：`../lume/bin/lume peer_node.lume`。

### 本机起一个「带 agent」的节点（验证提问链路）

单纯的 `peer_node.lume` 只有数据端点，`/peer/ask` 会 404 —— 它背后没有 agent
进程。要让看板的提问框可用，两个进程必须一起起：

```bash
make peer-up       # agent-httpd(:18099) + peer_node(:18092)，日志在 .runtime/
make peer-status   # 两个进程是否在
make peer-down     # 停掉
```

`make peer-up` **不会**自动改 `PEER_TARGETS` —— 探谁由人决定。要把自己也算进去：

```bash
PEER_TARGETS=127.0.0.1:18321,127.0.0.1:18092
```

验证：`python3 scripts/peers_fetch.py` 后看 `.data/peers.json` 里该节点的
`agent` 字段。`true` 才会出现提问框；`false` 表示它只有数据工具（如博客）。

### 提问要出真实答案，得配模型

不配 `LLM_API_KEY` 时 agent-httpd 用**离线 demo 引擎**：逐词流式回一段固定说明，
用来验证 SSE 通路，**不接任何模型**。所以「能问、有回答」≠「链路接了模型」。

```bash
export LLM_API_URL=https://api.deepseek.com/chat/completions
export LLM_MODEL=deepseek-chat
export LLM_API_KEY=<密钥>
make peer-down && make peer-up     # 重启才生效（读的是进程环境）
```

这三个是 **agent-httpd 的环境变量**，不是看板的。

## 跨机部署清单

两台机器上跑同一套仓库时，按下面逐项对齐。**任何一项不一致，症状都很像"网络不通"**
（连接被拒 / 全部 401 / 节点显示离线），所以先按这张表对，再去查网络。

| 项 | 机器 A | 机器 B | 不一致的症状 |
|---|---|---|---|
| `PEER_SHARED`（或 `PEER_ROOT`） | 同一个值 | 同一个值 | 全部 401 `unauthorized:true` |
| `PEER_NAME` | 各自不同（`nodeA` / `nodeB`） | 同左 | 两个节点互相覆盖成同一个 id |
| `PEER_BIND` | `0.0.0.0` | `0.0.0.0` | 从对方连过来是「连接被拒」 |
| `PEER_PORT` | 各应用固定（见下表） | 同左 | 探测超时 |
| `PEER_TARGETS` | 写**对方**的 `LAN_IP:端口` | 写对方的 | 节点列表里看不到对方 |
| 时钟 | 与对方偏差 < `PEER_SKEW_SECS` | 同左 | 随机 401（重放窗口外） |

端口由各应用 Makefile 的 `PEER_PORT_RUN` 固定，不要改 `.env` 里的 `PEER_PORT` 期望覆盖它：

| 应用 | peer 端口 | 说明 |
|---|---|---|
| `lume-lan` | 18092 | 看板，浏览器直连，通常必须 `0.0.0.0` |
| `lume-crm` | 18102 | 客户/商机统计（只读） |
| `lume-scanner` | 18103 | A 股扫描结果（只读） |
| `lume-blog` | 18321 | 文章/统计（只读） |

### `PEER_BIND` 从哪来

`make peer-up` 会从**本仓库的 `.env`** 读 `PEER_BIND`，没写才用 `127.0.0.1`。
改 `.env` 就生效，不用动 Makefile。

> 历史坑：这几处曾经在 Makefile 里硬编码 `PEER_BIND=127.0.0.1`，于是 `.env` 里
> 写 `PEER_BIND=0.0.0.0` 完全不生效，而文档说"配 PEER_BIND 即可"。这种
> **配了不生效**比不支持更难排查——对端拿到的是连接被拒，一路被当成网络问题查。
> 现在 `.env` 优先，且 `make -n peer-up` 能直接看到最终注入的值。

自查命令（不用真起进程）：

```bash
make -n peer-up | grep -o 'PEER_BIND=[^ ]*' | sort -u
# .env 未设 → PEER_BIND=127.0.0.1
# .env 设了 0.0.0.0 → PEER_BIND=0.0.0.0
```

### 步骤

1. **两边都置 `PEER_BIND=0.0.0.0`**（各应用 `.env`），起：

   ```bash
   make peer-up        # 各自的 peer_node
   make peer-status    # 确认在听，且看得到绑定地址
   ```

2. 确认端口真在对外监听（而不是只听回环）：

   ```bash
   lsof -nP -iTCP:18092 -sTCP:LISTEN     # 期望 *:18092，不是 127.0.0.1:18092
   ```

3. **在 A 上把 B 加进探测列表**（`lume-lan/.env`）：

   ```bash
   PEER_TARGETS=127.0.0.1:18321,127.0.0.1:18102,127.0.0.1:18103,192.168.x.b:18092
   ```

   `PEER_TARGETS` 只影响"探测谁"，不影响"谁能连我"。反向也要连就见 B 的配置。

4. 验活（不经过看板，直接测签名链路）：

   ```bash
   PEER_SHARED=<同一个密钥> PEER_NAME=nodeA PEER_HOST=192.168.x.b:18092 \
     ../lume/bin/lume peer_probe.lume
   ```

   全过 exit 0。也可以直接看 Agent Card：

   ```bash
   curl -s http://192.168.x.b:18092/.well-known/agent-card.json | head -c 300
   ```

   返回 200 且带 `name`/`skills` = 网络与绑定都对；**这一步不需要签名**，所以它能
   把"网络问题"和"密钥问题"分开。

5. 看板侧：`make peers` 拉一遍，`.data/peers.json` 里出现对方节点，`agent: true`
   才会出现提问框。

### 跨机时的额外注意

- **自注册目录是本地文件**（`PEER_REG_DIR`，默认 `.data/peers.d`），不会跨机器同步。
  所以跨机必须用**白名单**（`peers-identity.json` / `PEER_TARGETS`）互相登记，
  不能指望 A 自动发现 B。
- **`PEER_SHARED` 是对称密钥**：任何持有它的节点都能冒充任何节点名。跨机等于
  把"所有参与机器"放进同一个信任域——一台沦陷，等于全部沦陷。企业场景应换成
  非对称签名（见「已知风险」）。
- **传输是明文 HTTP**（见「明文传输的边界」），只传状态与任务指令，不传敏感串。
  需要加密就在 lume 外做（WireGuard / SSH 隧道 / 反代 TLS）。
- **不要暴露到公网**，也不要做端口转发。`/peer/invoke` 有工具白名单
  （见下节「`/peer/invoke` 的工具白名单」），但它是"拒绝清单 + 危险动词"风格，
  不是穷举许可 —— 能挡写/执行类动作，不等于能挡一切未知工具。

## 认证模型

签名放在 query 里（**不是 header**，见下方「实测约束」第 1 条）：

```
sig = hmac_sha256(PEER_SHARED, node \n ts \n nonce \n scope)

GET  → scope = "GET "  + path_only
POST → scope = "POST " + path_only + "\n" + body
```

`scope` 把签名绑到具体动作：签名不能挪用到别的端点，POST 的 body 被改则签名失效。
服务端校验时间窗（`PEER_SKEW_SECS`，默认 120s）后做常量时间比较。

三条安全底线：
1. **`PEER_SHARED` 为空时整个 `/peer/*` 关闭**，不是放行。
2. 密钥独立于看板的 `htpasswd` —— 一台机器沦陷不会连带另一台。
3. 未签名 / 错密钥 / 过期 / 篡改，一律返回 `{ok:false, unauthorized:true}`。

### 两种模式：对称（默认）与非对称（可选）

上面那套是 **HMAC 对称**：所有节点共一个密钥，谁持有它，谁就能用**任何**节点名去签。
也就是说它能防误连（配错 `PEER_TARGETS`、复制了别人的 `.env`），但**防不住恶意节点**。

要根治这个，配 `PEER_PRIVKEY` 切到 **ed25519 非对称**：

```
sig = ed25519_sign(signing_string, 本节点私钥)
验签 = ed25519_verify(signing_string, sig, 按 node 名查到的公钥)
```

待签串与对称模式**完全相同**（`node \n ts \n nonce \n scope`），只换算法 ——
这样"有没有换过算法"不会体现在协议输入上，排障时可以拿同一个串两边对。

| | 对称 HMAC（默认） | 非对称 ed25519 |
|---|---|---|
| 配置 | `PEER_SHARED` 一个共享密钥 | 每节点 `PEER_PRIVKEY` + 对端公钥表 |
| 防误连 / 防串台 | ✅ | ✅ |
| 防冒充 | ❌ 持钥者可冒充任意节点 | ✅ 没有别人私钥就签不出 |
| 泄漏影响面 | 整个信任域沦陷 | 只影响泄漏的那一台 |
| 新增节点 | 分发同一个密钥 | 交换公钥 |

**为什么默认不切**：两种模式不能混跑——一边签一边验算法不同，会全部 401。
切换需要所有节点同时改配置，这个决定应该由部署者做，不该由升级悄悄替你做。

### 凭据优先级（配了多个时谁生效）

三套凭据，优先级从高到低：

```
PEER_PRIVKEY  (ed25519)  >  PEER_ROOT  (derived)  >  PEER_SHARED  (shared)
```

**同时配多个时，低优先级的静默失效。** 这不是假设——实测四个节点里就有
`PEER_SHARED` 和 `PEER_ROOT` 都配着的，实际跑的是 `derived`，从配置文件上
完全看不出来。拿共享密钥去构造签名会一路 401，现象很像"密钥抄错了"。

所以 `/peer/health` 会返回实际生效的模式，并在有配置被忽略时给出警告：

```json
{
  "ok": true, "node": "crm", "peer": "client",
  "mode": "derived",
  "warnings": ["PEER_SHARED is set but ignored: PEER_ROOT takes precedence (mode=derived)"]
}
```

启动日志同样会打出来（只在有警告时）：

```
[peer] signature mode: derived
[peer] WARN PEER_SHARED is set but ignored: PEER_ROOT takes precedence (mode=derived)
```

看到这条就说明配置里有冗余项，删掉低优先级那个即可——**不要靠猜**。

启用步骤（每台机器都做）：

```bash
# 1. 生成密钥对，私钥写文件、公钥记下来
../lume/bin/lume -e 'print(json(ed25519_keypair()))'
# => {"sk":"<64 hex>","pk":"<64 hex>"}

# 2. 私钥落盘，只自己可读
echo "<sk hex>" > .data/peer.sk && chmod 600 .data/peer.sk

# 3. .env 里指过去
PEER_PRIVKEY=.data/peer.sk
PEER_PUBKEYS=.data/peers-pubkey.json

# 4. 把每台机器的 node → pk 汇总进 peers-pubkey.json
#    {"crm":"...","scanner":"...","blog":"..."}
```

公钥必须**通过可信渠道**登记（手工汇总、配置管理），不能从请求里取 ——
从请求里取公钥等于让中间人自证身份。

实测过的失败形态，都返回 `{ok:false, unauthorized:true}` 且错误信息能定位问题：

| 情况 | error |
|---|---|
| 对方公钥没登记 | `no public key for peer 'x' (Peer needs PEER_PUBKEYS or self-registration)` |
| 拿自己的私钥自称别人 | `bad signature for peer 'x'` |
| 时间戳超窗 | `ts outside window (9999s)` |

### 内置函数

`ed25519_*` 已进 lume 语言层（`lume-core/src/compat/ed25519/`，RFC 8032 纯 C 实现），
四个内建，全部 hex 字符串进 hex 字符串出：

```lume
ed25519_keypair()                    // → {sk, pk}
ed25519_pubkey(sk_hex)               // → pk_hex
ed25519_sign(msg, sk_hex)            // → sig_hex
ed25519_verify(msg, sig_hex, pk_hex) // → bool（格式非法返回 false，不抛错）
```

`verify` 对畸形输入返回 `false` 而不是报错：抛错会变成 500，让"有人伪造签名"
看起来像服务故障。

⚠️ 该实现**未做侧信道加固**（没有常量时间标量乘）。对节点身份认证这个场景
（固定身份、低频调用）够用；若要保护高价值密钥，应换 libsodium 之类加固实现。

## 实测踩到的坑

**博客节点在数据库被写入时静默返回 0。** 实测中 `blog.stats` 连续返回
`post_count: 0`，而同一时刻数据库明明有 102 篇。排查结论：对端 `lume-blog`
在读 `.data/blog.db` 时，若另一个进程正在写（`make migrate` / 后台保存），
`sql_query` 会拿到空结果而不是报错，于是 peer 把 `0` 当成真实数据转发了出去。

后果比"返回错误"更糟：**agent 会把它当作事实回答**（实测 agent 说"你的博客是空的"）。
暂时没有在 peer 侧修（这是博客 SQLite 层的并发行为，跨仓库），但两点值得做：

- 消费方：对「0 篇文章 / 0 浏览量」这类结果保持怀疑，必要时重试一次；
- 产出方：博客侧应给 `sql_query` 加锁等待或重试，而不是让空结果直接过。

**`server` 是 lume 的保留字。** 在 map 字面量里不能写裸键 `server:`，
必须 `"server":`——即使同一个 map 里其它键都是裸的。`when_to_use` 同理
（含下划线的键在部分位置会触发解析歧义，统一用字符串键最稳）。

## A2A 兼容层

peer 节点同时暴露**两套接口**，各司其职：

| 接口 | 谁能用 | 用途 |
|---|---|---|
| `/peer/tools` `/peer/invoke` | 本仓库的 `peer_bridge.py` | 轻量私有协议，agent 工具发现 |
| `/.well-known/agent-card.json` + `/a2a` | **任何 A2A 客户端** | 对外互操作（Claude Desktop / LangGraph / 云厂商） |

实现见 `src/a2a.lume`。版本口径按 **A2A v1.0**：能力端点在 `supportedInterfaces`
数组里声明；同时保留 v0.3 的单个 `url` 字段兜底（多数实现同时认这两个）。

### 已实现 / 未实现

| 能力 | 状态 |
|---|---|
| Agent Card 发现（`/.well-known/agent-card.json`） | ✅ |
| `message/send`（同步任务） | ✅ |
| **`message/send` + `configuration.async`（长任务）** | ✅ 立即返回 `submitted` |
| **`tasks/get`（轮询状态）** | ✅ |
| **`tasks/cancel`（取消）** | ✅ 含 `-32002` 已终结不可取消 |
| `message/stream`（SSE 流式） | ✅ 需 `lume-core` 版二进制，见下方「流式」 |
| 鉴权（`security` 字段 / OAuth） | ❌ 局域网 + 只读白名单兜底 |

**故意不谎报能力**：`streaming=false` 是诚实声明。若声称支持流式但没实现，
客户端会等一条永远不来的 SSE 流——这比直接说不支持糟糕得多。

### 节点类型：`capabilities.agent_backed`

A2A 规范允许扩展字段，这里加了一个 `agent_backed`：

- `true`（lume-lan）：背后是会推理的 LLM agent，模糊问题能正常回答
- `false`（lume-blog）：固定查询节点，认不出意图就回 `input-required`

这个区分有实际意义：验证脚本对两类节点期待不同，之前用 skill 名字猜类型失败过
（agent 节点的 skill 也叫 `fs__read_file`），改成节点自报才可靠。

### 长任务模型

**为什么需要**：`pse-review` 要跑 3-10 分钟。同步的 `message/send` 只能硬等，
断网就全丢，中间没有任何进度。A2A 的解法是 Task 生命周期 —— 客户端拿
`taskId` 轮询，而不是占着一条连接。

```
客户端 ──message/send{async:true}──> 节点
       <── {state:"submitted", id:"..."}    立即返回, 不阻塞
                                      ↓
                          宿主 a2a_worker.py 扫到 submitted
                                      ↓ working → 执行 → completed
客户端 ──tasks/get{id}──────────────> 节点
       <── {state:"completed", artifacts:[...]}
```

**为什么执行必须放宿主**：lume 没有 `spawn`/`exec`，`http_*` 又是收完整响应
才返回的同步调用。「后台慢慢跑」在纯 lume 里做不到。所以职责这样分：

| 层 | 职责 |
|---|---|
| `src/a2a_tasks.lume` | 状态机 + 持久化（`.data/a2a-tasks/<id>.json`） |
| `scripts/a2a_worker.py` | 真正的异步执行（扫 submitted → 跑 → 写回结果） |

**存储用单文件不用 SQLite**：`sql_query` 在别的进程写入时会**静默返回空**
（本项目实测踩过，见下方「实测踩到的坑」）。任务状态比查询结果重要得多，
丢不起——单文件 + 文件锁，行为可预测。

**状态流转实测**（`submitted → working → completed`，以及 `canceled`）：

```
task ce1727...: working (kind=peer_invoke)
task ce1727...: completed (103 chars)
tasks/get → state: completed, post_count: 102
tasks/cancel(已终结) → -32002 task not cancelable
tasks/get(不存在)   → -32001 task not found
```

### input-required 多轮续发

任务中途需要人确认时（比如 `pse-review` 要跑付费模型），停在 `input-required`
让客户端问用户，而不是硬跑或猜测。

**协议约定**（按 A2A 多轮规范）：
- `status.message` 是**告知客户端需要什么**的地方，与 `artifacts` 分开：
  - `artifacts` = 已产出的内容
  - `status.message` = 当前状态要说的话（通常是提问）
- 客户端续发时**带同一个 `taskId`**：`message/send` 的 `params.id`
- **任务不可变性**：已 `completed`/`failed`/`canceled` 的任务不能重启，续发回
  `-32004`，追问应在同一 `contextId` 下开新任务

实测完整回路：

```
提交(require_approval) → state: submitted
worker 扫到               → state: input-required
                            status.message: "将查询博客统计，确认执行请回复 yes"
客户端 message/send{id:同一taskId, "yes"} → state: submitted
worker 再扫               → approved by client → working → completed
tasks/get                 → post_count: 102, history: 1
```

错误路径实测：对已完成任务续发 → `-32004`；对不存在任务续发 → `-32001`。

**实现要点**：
- 审批门在 worker 侧（`payload.require_approval`），不在 lume 侧 —— 因为
  "要不要问、问什么"属于执行策略，不是协议层的事
- 用户回答记进 `history`，worker 用 `last_user_text()` 读取并判断是否批准
- 批准后打 `_approved` 标记，避免续发后反复卡在同一道门

### 审批判定：精确匹配，不做意图猜测

最初实现是 `any(w in text for w in APPROVAL_WORDS)` —— **包含匹配**。于是：

| 用户说 | 旧实现 | 现在 |
|---|---|---|
| `yes` | ✅ 批准 | ✅ 批准 |
| `好像可以` | ✅ 批准 | ❌ 继续等 |
| `不 yes 吧` | ✅ 批准 | ❌ 继续等 |
| `是它吗` | ✅ 批准 | ❌ 继续等 |
| `可以吗？` | ✅ 批准 | ❌ 继续等 |

只读场景误批代价小（重跑一次），但一旦接写操作就成了**真实的授权漏洞**，
而且比没有审批更危险 —— 它给出"已确认"的错觉。

现在只认归一化后**完全等于**白名单里的词（`yes` / `确认` / `批准` / `ok` / `approved` …），
句末标点容忍，其余一律不批。**宁可多问一轮。** 19 个用例覆盖，见
`tests/test_a2a_worker.py`。

### 审计：`a2a-audit.jsonl`

形状照 `knowledge/shared/abac.lume` 的 `audit_write`（JSONL 追加，`at` 是 unix 秒）。
任务文件的 `history` 只记对话，**不记决策** —— 没有审计就查不出"谁在什么时候
批准了什么"。

```
{"event":"approval_required","task_id":"...","tool":"blog.stats",
 "target":"127.0.0.1:18241","payload_digest":"a7e7984617ef5323",
 "answer":"好像可以","decision":"PENDING","at":1793237635}
{"event":"approval_granted","task_id":"...","answer":"确认","decision":"ALLOW",...}
{"event":"task_finished","task_id":"...","state":"completed","result_len":110,...}
```

`payload_digest` 是归一化 payload 的 sha256 前 16 位 —— 记摘要不记全文，
既能控体积，又能证明「批的就是跑的这个」（参数一变摘要就变）。

实测链路：`PENDING`（空回答）→ `PENDING`（"好像可以"被拒）→ `ALLOW`（"确认"）→ `task_finished`。

### 第二份审计：`a2a_call`（调用本身）

上面的 `approval_*` 回答的是**决策**问题（谁批了什么）。但排障时问的是另一个
问题：**这一步到底发出去没有、走到哪一层断的**。两者混在一起会互相干扰，
所以单列一类事件：

```json
{"event":"a2a_call","outcome":"ok","status":200,"elapsed_ms":0,
 "target":"host.docker.internal:18321","method":"message/send",
 "detail":"state=submitted role=agent_reviewer approve","origin":"lan-ui"}
```

`outcome` 刻意是**分类**而不是布尔：

| outcome | 含义 | 该查什么 |
|---|---|---|
| `ok` | 对端正常处理 | — |
| `unreachable` | 没连上 | 地址改写、隧道、容器网络 |
| `denied` | 连上了但被拒 | 身份是否在 `peers-identity.json` 里 |
| `bad_response` | 连通了但答的不对 | 对端版本、中间代理 |

只记 `false` 的话，日志里全是一样的失败，分不出该查哪一层。

### 两份审计为什么都要

同一次调用可能在**两侧各有一条**：

- `source: "lan-ui"` —— 看板自己发出的那一跳（`/app/audit/`，容器内追加写）
- `source: "<执行方>"` —— 执行方收到并处理的（`<state>/.data/a2a-audit.jsonl`）

这个组合本身就是诊断信息：

| 看板侧 | 执行方侧 | 说明 |
|---|---|---|
| 有 | 有 | 完整往返，正常 |
| 有 | 无 | **发出去没到**（这一侧是唯一线索） |
| 无 | 有 | 不是从看板发起的（脚本 / CLI） |

容器 `.data` 是 `:ro` 挂载的（看板被攻破不该能改宿主状态），所以看板侧的
审计放在单独的可写卷 `./audit:/app/audit`；「改状态」和「记流水」权限分开。

> 注意 lume 只有 `write_file`（原子覆盖），没有 append。追加只能
> 「读全文 → 拼新行 → 整体写回」，因此单文件到 256 KiB 会轮转一份 `.1`。

### 多级审批链（agent 出场即带角色）

**角色不是等级。** 审批链是职责分离（SoD）：

| 角色 | 允许的动作 |
|---|---|
| `agent_operator` | `execute` / `request` |
| `agent_reviewer` | `review_first` |
| `agent_reviewer_second` | `review_second` |
| `human_admin` | `approve` / `override` / `audit` |

**为什么不用 `role_level(x) >= min_role`**：那样 `human_admin` 会自动继承
`agent_operator` 的权限 —— 而审批链的语义恰恰相反：**终审者不得绕过流程直接执行**。
这里用「角色 → 允许的动作集合」，动作互斥是结构性的，不靠约定。

实测：`human_admin` 能 `approve` 但 `can(execute) == false`。

**链由风险决定**（`lume-core/lib/approval.lume` 的 `chain_for`）：

| risk | 链 |
|---|---|
| `peer` | 单级 `agent_reviewer` |
| `low` | 单级 `human_admin` |
| `medium` / `high` / 未知 | 三级 `agent_reviewer → agent_reviewer_second → human_admin` |

**未知风险按最严处理**，不是放行。

**客户端怎么批**：回答 `<role>: approve` 或 `<role>: reject <理由>`；裸 `yes`
视为用当前待审角色批。认不出就继续等（见下方「精确匹配」）。

实测完整链路：

```
需 agent_reviewer 审核  →human_admin: approve  被拒(not your turn)
agent_reviewer: approve        → next agent_reviewer_second
agent_reviewer_second: approve → next human_admin
human_admin: approve           → completed
```

**越权抢批被拒**是这条链最重要的行为：`human_admin` 在一审之前批，回
`not your turn (need agent_reviewer, got human_admin)`，任务继续停在原关。

**规则有两份实现，必须一致**：
- `lume-core/lib/approval.lume`（A2A 端点用）
- `scripts/a2a_worker.py` 里的 Python 镜像（worker 用）

一份说"轮到你了"、另一份说"没轮到"，任务会永久卡在 `input-required` 且两边
日志都正常 —— 极难排查。所以有 `tests/test_approval_chain.py` 做**三方一致性
比对**：同一组 8 个场景分别喂给 lume 与 Python，逐项比对
`(ok, done, next_role, rejected)`。

### 领域 → 谁审：二审跨领域

**光有角色还不够。** `agent_reviewer` 只是"有个审查者"，但**谁有资格当这个
审查者，取决于被审的是什么**：`lume-scanner` 对股票数据有专业判断力，对
"发布一篇博客"却无从判断。

所以映射的键是**领域**，值是"这个领域里谁来审"（`lume-core/lib/domains.lume`）：

```json
{
  "domains": {
    "stock-data": {
      "first":  { "node": "scanner", "why": "懂指标与数据完整性" },
      "second": { "node": "crm",     "why": "跨域复核：从'变更是否留痕'角度提问" }
    }
  }
}
```

**二审刻意跨领域。** 不是"同领域更高级的专家"，而是**另一个领域**的节点：

- 同领域两人容易**同源偏差**（看法一致，等于没复核）
- 换个领域的人会问"这份数据凭什么可信"——恰好是专业细节之外最该被问的
- 代价：二审未必懂细节，所以否决理由要写清，一审可据此补证

**未登记领域一律回退 `human_admin`，绝不无人审。** 新加的 lume-* 应用没登记
领域时也必须有人过一眼；登记错（只有 second 没 first）同样兜底 —— "没有一审
就二审"语义不成立。

实测提示（`stock-data` 领域）：

```
即将执行 blog.stats，当前需 agent_reviewer（scanner） 审核。
（lume-scanner 负责全市场扫描，懂指标与数据完整性）
回复「agent_reviewer: approve」或「agent_reviewer: reject <理由>」
```

审计里记的是**完整链快照**（含 node 与 why），而不只是角色名 —— 事后能回答
"这次为什么是 scanner 审的"。

**配置位置**：`.data/domains.json`（宿主维护，gitignore）。样例见
`.data/domains.example.json`。集中一份而不是各应用自带：跨域复核要求"知道
别的领域有哪些节点"，分散在各应用里做不到。

### 流式（message/stream）

lume-core 的路由现在支持返回 `{ sse: [...] }`：框架逐条写出 `data: <json>` 事件，
不设 `Content-Length`（SSE 靠关闭连接表示结束）。

**前提：必须是带 SSE 支持的 `lume-core` 二进制。** 框架版 `lume`（agent-httpd
那套 serve）是另一套实现，不认 `sse` 字段——用它会**静默降级成 JSON**
（客户端拿到 `{"sse":[...]}`，不是流）。

**客户端必须声明 `Accept: text/event-stream`。** 没声明时框架降级为普通 JSON
一次性返回。这是有意为之：浏览器或会缓冲的中间层收到流式响应时，页面会"空白
到结束"，反而更糟。降级后至少拿到完整数据。

实测响应头与事件：

```
HTTP/1.1 200 OK
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache
X-Accel-Buffering: no          ← 让 nginx 不缓冲，否则等于没流式
（无 Content-Length）

data: {"...","kind":"status-update","status":{"state":"working"},"final":false}
data: {"...","kind":"artifact-update","artifact":{...},"lastChunk":true}
data: {"...","kind":"status-update","status":{"state":"completed"},"final":true}
```

### ⚠️ 这是「同步回放」，不是「边跑边看」

lume 没有异步原语，handler 必须返回后才能发响应。所以事件是**执行完之后
一次性生成的**，不是边执行边推。

对客户端而言**语义正确**（收到同样的事件序列、同样的 final 标记），但拿不到
中间进度。真要看长任务进度，用 `message/send` + `async` + 轮询 `tasks/get`。

这两条路的区别：

| 方式 | 拿到什么 | 何时用 |
|---|---|---|
| `message/stream` | 规范的事件序列，但一次性到达 | 需要 SSE 兼容的客户端、结果快 |
| `message/send{async}` + `tasks/get` | 真实中间状态 | 长任务（pse-review 那种） |

### 身份可信：每节点一把独立密钥

**问题**：原协议所有节点共用一个 `PEER_SHARED`。签名里虽带 `node`，但**任何
持钥者都能用别人的 node 名去签**。于是领域映射里的"谁审"只是排班意图，审计里
的 actor 也只是自称。

**解法**：按节点派生密钥，服务端按**声称的 node** 取同一把密钥验签。

```
key(node) = hmac_sha256(PEER_ROOT, "peer:" + node)
```

- 部署只需分发一个 `PEER_ROOT`，各节点用它 + 自己的 `PEER_NAME` 算密钥
- 节点白名单在 `.data/peers-identity.json`（可热改，撤销不用重启）
- **未登记节点直接拒**，不给验签机会

实测三种情况：

```
正常(blog 用 blog 密钥)  → {"ok":true,"peer":"blog"}
冒充(lan 冒充 blog)      → bad signature for peer 'blog'
冒充(自称 scanner)       → bad signature for peer 'scanner'
未登记节点               → unknown peer 'god' (not in registry)
```

**降级行为**：服务端配了 `PEER_ROOT`、客户端只给旧 `PEER_SHARED` → 验签失败。
这是**故意 fail-closed** —— 混用两种模式等于给了一个绕过身份的口子。迁移时
两端要同时切。

### ⚠️ 这层的边界：防误连，不防恶意节点

这是**对称密钥派生**：任何知道 `PEER_ROOT` 的节点都能算出别人的密钥，所以它
**能冒充**。真正的不可否认需要非对称签名（每节点私钥签名、服务端持公钥验签）。

为什么先这样：lume 内置只有 `hmac_sha256` / `sha256`，没有 ed25519 / RSA。上
非对称要么等内置支持，要么在宿主进程签名、lume 侧验签 —— 后者引入跨语言一致性
负担。

所以当前这层解决的是**实际最常见的故障**：

| 场景 | 是否拦住 |
|---|---|
| `PEER_TARGETS` 配错，连到了别的节点 | ✅ |
| 复制了别人的 `.env`，用自己的密钥连别人 | ✅ |
| 审计里 actor 是自称的、不可信 | ✅（现在密钥对不上就进不来） |
| 恶意节点主动冒充 | ❌ **拦不住**（需非对称） |

### 看板上的表现

「应用互访」Tab 分三块，数据全在宿主采集、容器只读：

| 区块 | 来源 | 说明 |
|---|---|---|
| 节点卡片 | `peers_fetch.py` 探测 `/peer/tools` + `/peer/info` | 可达性、延迟、工具名、统计数字 |
| **待办审批** | 扫 `PEER_STATE_DIRS` 下的 `a2a-tasks/*.json` | 只列**非终结态**任务；显示审批链进度、当前等谁 |
| **审计** | `a2a-audit.jsonl` 尾部 30 条 | 含越权被拒（红底），能看出"谁批了什么" |

**为什么要这两块面板**：审批链要求人点确认，而协议层只接受
`<role>: approve` 这种字符串。没有表单的话每次批准都要手打角色名，打错
等于没批（还会留一条 DENY 审计）。任务历史只记对话、不记决策，所以"谁批的"
只能从审计里看。

**数据从哪来**：任务与审计是**执行方**（blog 等）写的，看板只读文件。用
`PEER_STATE_DIRS=../lume-blog/.data` 指定。这是"谁有权限谁采集"的延续 ——
容器既不该拿凭据也不该写别人的状态目录。

**采集侧也要身份**：`peers_fetch.py` 支持 `PEER_ROOT`（按节点派生密钥）。
不配的话，对端一启用身份层，探测就全部 401，而 UI 上只显示"不可达"，
看不出是身份问题 —— 这个坑实测踩过。

### 与 kb-abac / kb-auth 的关系

**没有引入**，原因是不匹配：

| | kb-abac | 这里的审批 |
|---|---|---|
| 主体 | 人（账号 + 角色 + 阶段） | agent 发起的任务 |
| 模型 | `decide(user, resource, action, stage)` | 任务生命周期中的一个暂停点 |
| 状态 | 无状态（每次现算） | 有状态（history、可续发） |

而且 `kb-auth-shared` 的 README 明写「一个中央 `accounts.json` 不能同时做两个项目
的阶段权威」；lume-lan 不在 knowledge 站点族里，没有 stage 概念，硬接等于造一套
假资源表。**借用的是它的审计形状与 `safe_json` 的容错取舍，不是它的决策引擎。**

角色词表也不同：kb-abac 是 `student/teacher/admin`（教育场景），这里是
`agent_operator/agent_reviewer/agent_reviewer_second/human_admin`（审批链）。
两者语义不通用，所以审批链的规则放在 lume-core，**不改 kb-abac 那三个仓**。

将来若要接写操作、需要"谁有权批"的真实身份判断时，再按 ABAC 的形状做
（`configure()` 注入任务→策略映射，账号复用 kb-auth）。

**仍未实现**：`input-required` 时的**结构化输入 schema**。A2A #1016 讨论正在推
这个（让客户端渲染表单而不是解析自然语言），当前是纯文本提问。

### ⚠️ Agent Card 的 `url` 必须客户端可达

这是实测踩到的坑：配了 `PEER_PUBLIC_URL=http://192.168.1.10:18191` 但节点只绑
`127.0.0.1`，客户端照 card 里的地址去连直接 `ConnectionRefused`——**而 card 本身
字段齐全、看起来完全正常**，极易误判成客户端问题。

现在 `a2a.lume` 做两件事：
- 未配 `PEER_PUBLIC_URL` 时，用 `PEER_BIND:PEER_PORT` 拼（保证自洽）
- 检测到「绑 loopback 却宣告外部地址」时，在 card 里加 `warnings` 字段说明

### 验证

`scripts/a2a_probe.py` 用一个**独立 A2A 客户端**的视角做 21 项检查（不复用
bridge 的任何代码，只按规范走），覆盖发现、同步任务、长任务生命周期、多轮续发、错误码：

```bash
python3 scripts/a2a_probe.py http://127.0.0.1:18211
```

实测两个节点各 21 项全过，博客回 `文章 102 篇…`，lan 回 agent 的真实作答。

## `/peer/invoke` 的工具白名单

实现：`src/peer_tool_policy.lume`（纯函数，可单测，不依赖进程启动）。

**为什么 lan 需要而 crm/scanner 不那么需要**：crm/scanner 的工具是编译期写死的
只读查询；lan 背后是能调**任意 MCP 工具**的通用 agent。不设限的话，持凭据的对端
等于拿到了本机的文件写权限和记忆存储。

### 两级策略

区别不在动词，而在**改的是什么状态**：

| 级别 | 覆盖的命名空间 | 规则 |
|---|---|---|
| **STRICT**（跨调用留存） | `memory__` `git__` `fs__` `code__` `exec__` `shell__` `docker__` | **默认拒**，只放过只读白名单 |
| **SCOPED**（受限命名空间内生成） | `file__` `pptx__` `docx__` `xlsx__` `pdf__` `image__` | 允许创建/追加，**禁止覆盖删除** |
| 其它 | 业务数据查询等 | 只拦破坏性 + 执行类动词 |

为什么这样分——第一版把所有危险动词平铺成一张表，两头都出错：

- **漏判**：`memory__create_entities` / `memory__add_observations` 是写操作，
  但 `create`/`add` 不在表里。对端能往记忆里塞假事实，而塞进去的东西会被后续
  推理当成真的。
- **误伤**：补上 `create`/`add` 之后，`pptx__create` / `pptx__add_slide` 这些
  正常文档生成一起被拒，把一个可用功能变成不可用。

两者的差别不在动词而在状态：记忆/git/代码执行改的是**跨会话留存**的东西；
工作区内的文件生成影响面有界。所以分开处理。

### 实测行为

```
DENY   memory__create_entities    <- 跨边界写
DENY   fs__write_file             <- 跨边界写（MCP_FS_ROOT 可能被配得很宽）
DENY   git__push                  <- 仓库历史
DENY   shell_exec                 <- 任意代码执行
ALLOW  memory__read_graph         <- 只读白名单
ALLOW  fs__read_file              <- 只读白名单
ALLOW  pptx__create               <- SCOPED 生成，允许
DENY   pptx__delete_slide         <- SCOPED 但破坏性
ALLOW  crm__query                 <- 业务查询
```

**严格命名空间里没见过的名字默认拒**（fail-closed），因为那些工具一旦是写操作，
后果跨会话留存——不给"猜错"的空间。

### 清单会标注可用性，不隐藏

`/peer/tools` 为每个工具返回 `peer_callable` 与 `peer_blocked_reason`：

```json
{"name": "fs__write_file", "peer_callable": false,
 "peer_blocked_reason": "tool 'fs__write_file' under 'fs__' may change persisted state ...",
 "when_to_use": "peer 面不可调用（...）。本机 agent 内部仍可用。"}
```

**不隐藏**的理由：隐藏会让"为什么调不通"变成谜（对端压根不知道有这个工具）；
标注则让调用方在编排阶段就能排除，不必靠失败重试去发现。`when_to_use` 也一并
改写——agent 读的是这段文字，"慎用"和"不可调用"对它的行为影响完全不同。

### 边界（必须知道）

`/peer/ask` 与 `/a2a` **不经过这层**。它们走 agent 推理，agent 在推理过程中可能
自己决定调用工具。要收紧到那条路径，得在 agent-httpd 的工具注册表层面按调用来源
区分权限——那是另一个量级的改动。

把这层想成**关上了最容易开的那扇门**，不是封死了所有门。

## 已知风险

1. **`/peer/invoke` 的工具白名单（已实现，但有边界）。** 见下节。要点：
   - 跨边界状态（`memory__` / `git__` / `fs__` / `code__` / `exec__` 等）
     **默认拒**，只有只读白名单例外；
   - 受限命名空间（`pptx__` / `docx__` / `xlsx__` …）允许创建、禁止覆盖删除；
   - `/peer/ask` 与 `/a2a` **不经过**这层 —— agent 在推理中仍可能自己调工具。
     那条路径要收紧，得在 agent-httpd 的注册表层按调用来源分权限。
2. **明文传输**（见上节），签名不加密。
3. **agent-httpd 未配 Basic Auth 时启动会打警告**（`WARNING: listening on 0.0.0.0
   without Basic Auth`）。peer 只连本机 agent（`127.0.0.1`），所以这条警告不影响
   peer 链路，但如果那台机器的 agent-httpd 同时对外暴露，需要单独加认证。
4. **`fs` MCP 的根目录是 `MCP_FS_ROOT`**，agent-httpd 启动时会警告它包含 `.data`
   会话存储 —— 意味着通过 `fs__read_file` 能读到历史 transcript。跨节点开放
   `fs__*` 前请确认这个根目录范围。

## 实测约束（写代码前必须知道）

这些是 2026-10-28 在本机用真实 lume 进程验证过的，不是从文档推的：

1. **route handler 读不到请求头。** `req` 只有 `method` / `path` / `query` /
   `query_params` / `params` / `body` / `session`（见 `lume-core/src/bridge_serve.c`
   的 `make_req_map`）。所以 `X-Peer-Key` 这类自定义头方案**不成立**，凭据只能走
   query 或 body。
2. **`req.path` 包含 query 串。** 实测 `/a?x=1` → `path="/a?x=1"`。自己算签名 scope
   时要剥掉 query（`peer.path_only()`）。
3. **GET 的 `req.body` 是 null**，一律 `get(req, "body", "")`。
4. **出站必须 `allow_private: true`。** lume 的 SSRF 闸门在**开 socket 之前**就拒
   回环/私网地址；不加这个参数，报错长得像「节点离线」，实际是本地拦截。
5. **`http_*` 不是流式**：收完整个 body 才返回。对 SSE 端点（如 agent-httpd 的
   `/react/api/chat`）只能整体收，配合 `max_bytes` / `timeout`。
6. **不需要 `main()` 定义来设退出码**，而是顶层**真的写一行 `main();`** ——
   解释器在 `exec_program` 里拦截顶层 `main()` 表达式语句并取返回值。
7. **顶层标量在函数内赋值是复制**，改不到外层。可变累加器要用 map + `put()`
   （`peer_probe.lume` 曾因此把 3 个失败报成「全部通过」）。
8. 内建函数名与保留字：`head` 不能作函数名；类型标注用 `: type` 不是 `-> type`；
   `map` 不能作返回类型标注（省略即可）。
9. **`str_match` 的 `^`/`$` 不按多行匹配。** 对整段 SSE 用 `"^data: (.*)$"` 会返回
   **0 条**。必须用换行结尾的模式 `"data: ([^\n]*)\n"`，并给文本补一个尾随换行，
   否则最后一帧（`done`）抓不到。
10. **`import` 路径相对脚本所在目录**，不是相对 cwd。根目录脚本写
    `"src/peer/agent.lume"`，放在子目录里的脚本要相应调整。
11. **agent-httpd 的 `-p` 可能被 `.env` 的 `PORT` 覆盖**，启动后要用
    `lsof -nP -iTCP -sTCP:LISTEN` 确认实际端口，别假设是命令行给的那个。
12. **`tool` 既是参数名冲突也是 map 键冲突。** `func f(tool: string)` 报
    `expected parameter name`，`{tool: x}` 报 `expected map key`。用 `tool_name`
    和字符串键 `"tool"`。
13. **`mcp.h` 的注释与实现不一致**：写着 tools/call 是 fresh spawn，实际
    `mcp_tools_call_raw()` 复用常驻连接。以 `.c` 实现为准。

## 明文传输的边界

lume 的 `http_*` 不支持自定义 CA，内网自签证书走不通，所以跨机是**明文 HTTP**。
签名保证不可伪造 / 不可篡改 / 不可重放，但**不加密载荷**。

因此经 peer 通道只传状态与任务指令，**不要传密码、密钥、证件号、DNS 明细等敏感串**。
若确实需要加密，得在 lume 之外加一层（例如 WireGuard 或 SSH 隧道）。

## 现状与下一步

已实现：健康探测、能力发现（静态清单）、**跨节点 agent 对话**、完整签名校验。

### 跨节点 agent 对话

`POST /peer/ask {question, sessionId?}` → 对端把提问转发给它自己的 agent-httpd
（`AGENT_URL`，默认 `http://127.0.0.1:18099`），拿回完整回答：

```json
{"ok":true,"node":"nodeB","from":"nodeA","question":"...","answer":"...",
 "notes":[],"error":"","complete":true,"events":18,"agent_status":200}
```

实现要点（`src/peer/agent.lume`）：

- agent-httpd 的 `/react/api/chat` **只出 SSE**，而 lume 的 `http_post` 收完才返回，
  所以是「整体收 → 逐帧解析 → 拼回文本」。没有中间反馈，靠 `AGENT_TIMEOUT`
  （默认 90s）控制总时长、`max_bytes` 1 MiB 兜底。
- 每帧用 `json()` **全文解析**，不要自己按引号切分 —— delta 里可能含 `\"` 和 `\n`。
- `complete=false` 表示没收到 `done`，回答可能被截断（超时/超上限），调用方要判。
- `sessionId` 透传给 agent-httpd，它按会话存 transcript/facts，多轮对话不断线。
- agent 没起时 `/peer/ask` 返回 `{ok:false, error:"agent 不可达: ..."}`，
  `/peer/agent` 可先探测再提问，**不会静默返回空答案**。

已验证：A→B 与 B→A 双向各拿到真实 agent 回答（`complete=true`），
agent 掉线时降级信息明确。

**尚未实现**：

- **流式回传**：现在是「等回答完再返回」。要逐字回传得让 lume 侧自己写流式响应
  （`http_*` 做不到），或者改成「提交任务 + 轮询」。
- **nonce 去重**：当前靠时间窗防重放（120s 内同一 nonce 重放仍会通过）。如需更强
  保证，服务端要维护已见 nonce 表（lume 的 `req.session` 是每会话的，不适合做全局表）。
- **多节点**：现在一次只探一个 `PEER_HOST`；多节点需要外层再套一层循环。

### 跨节点工具调用

依赖 agent-httpd 新增的两个端点（见下节）：

```
GET  /peer/tools   → {"ok":true,"node":"nodeB","tools":[{"name":"echo__pong","server":"echo","desc":"..."},...]}
POST /peer/invoke  → {"tool":"<name>","arguments":{...}}
                   ← {"ok":true,"node":"nodeB","from":"nodeA","tool":"...","result":"...","error":""}
```

`tools` 现在返回**真实 MCP 注册表**（本机 agent-httpd 的 `mcp_tool_count()`），
不再是静态清单；agent 没起时降级为 `tools:[]` + `error` 说明。

`/peer/invoke` 是**执行**类操作，安全上依赖三层：签名覆盖 body（改参数即失效）、
凭据的共享范围、以及**工具白名单**（见下节）。

实测：跨节点拉到 28 个工具，远端执行 `echo__pong` 与 `fs__list_allowed_directories`
均成功，未知工具返回 `502 + "error: unknown mcp tool ..."`。

## 让 agent 自己发现并调用 peer 能力

这是 peer 协议真正的用途：**agent 不需要预先知道局域网里有什么**，它每轮都能
看到一张聚合后的工具表，自己决定调哪个。

```
agent-httpd ──stdio+MCP──> scripts/peer_bridge.py ──HTTP+HMAC──> peer 节点 A
                                                              └─> peer 节点 B
```

**为什么需要这个桥**：agent-httpd 的 MCP 客户端只支持 **stdio**
（`src/agent/mcp.c:119` 明写 `only stdio transport today`），而 peer 能力在
HTTP 后面。桥就做这一层转换：收 `tools/list` → 汇总各节点的 `/peer/tools`；
收 `tools/call` → 转到对应节点的 `/peer/invoke`。

**工具命名**：`<node>__<tool>`，例如 `blog__blog.stats`、`lan2__fs__read_file`。
前缀保证不同节点的同名工具不互撞（与 agent-httpd 自己的 `<id>:<tool>` 同一思想）。

**自动发现**：每次 `tools/list` 都重新探测，所以新增一个 lume-* 应用只要它起
自己的 `peer_node.lume`，**桥不用改一行代码**，agent 下次就能看到。

实测（接入 2 个节点）：

```
peer 系列工具分属两个对端应用：lan2（提供 fs / memory / think / pse-review /
portfolio-check / echo）和 erishen's blog（blog.stats、blog.recent）。
```

—— agent 自己得出了这个结论。

### 暴露策略（重要）

这里说的是**客户端**（peer_bridge）的可见性过滤。**服务端**另有一层工具白名单
（见下节「`/peer/invoke` 的工具白名单」），两层职责不同：

| | 谁管 | 作用 |
|---|---|---|
| 服务端 `peer_tool_policy` | 被调用的节点 | 决定"能不能执行"，是**权威** |
| 客户端 `PEER_EXPOSE` | 发起调用的桥 | 决定"要不要展示给 agent"，是**偏好** |

服务端在 `/peer/tools` 里为每个工具返回 `peer_callable` 标注，客户端优先采信它
——服务端比客户端更清楚自己配了哪些 MCP、哪些被策略拦下。客户端再维护一份独立
词表只会和服务端漂移，而漂移的方向通常是"客户端以为能调、服务端拒绝"，表现为
agent 反复试一个永远失败的工具。

桥默认**只暴露只读工具**：

| `PEER_EXPOSE` | 行为 |
|---|---|
| `readonly`（默认） | 按名字过滤掉 write/edit/create/delete/move/patch/… |
| `all` | 全暴露（风险自担，建议仅在隔离网络） |
| `k1,k2` | 白名单：只暴露名字含这些关键字的工具 |

被拦下的工具不会消失得无影无踪——桥会在工具表里附一条 `_peers__blocked`，
写明拦了哪些，让 agent 知道「存在但被禁用」，而不是以为对端没这个能力而绕路
或编造。实测 2 个节点共 32 个工具，`readonly` 下暴露 20 个只读 + 1 条说明，
`fs__write_file` / `fs__edit_file` / `memory__delete_*` 等 10 个写工具被拦。

## agent-httpd 侧的配套改动

跨节点调工具需要 agent-httpd 有 HTTP 入口（原来只有 `/react/api/chat`），
因此在其仓库新增：

| 文件 | 改动 |
|---|---|
| `src/agent/tools_api.c` | **新增** `GET /react/api/tools`、`POST /react/api/tools/call` |
| `src/agent/tools_api.h` | **新增** 接口声明 |
| `src/http/http.c` | 分发链接入（chat 之后、SSR 转发之前） |
| `src/http/http_route.c` | 从快速路径与 SSR 中继排除该前缀 |
| `Makefile` | 源文件加入构建 |

行为约定：
- `/react/api/tools/call` **POST-only**（CSRF：状态变更操作不能由 `<img>` 触发）
- 同源校验复用 chat 的语义：有 Origin 则必须匹配 Host，无 Origin（机器调用）放行
- `arguments` 缺省视为 `{}`（MCP 允许省略）；**存在但格式错**才 400
- `mcp_call` 的 `error: ` 前缀失败会转成 `{"ok":false}` 并返回 502，便于调用方按字段分支

**性能更正**：早前把 `mcp.h` 注释里的「fresh spawn」当成了实现，实际
`mcp_tools_call_raw()`（`mcp.c:483`）**复用常驻连接**，只有首次或连接挂掉才重新握手。
头文件注释与实现不一致，是上游文档缺陷。

不建议使用 `AGENT_TOOL_SOURCE=gateway`：agent-httpd 源码
（`src/agent/agent.c:57`）已注明那是未完成的草稿，会把 `get_time` 降级为 UTC 并
丢掉 domain MCP，明确写着「修好之前不要启用」。
