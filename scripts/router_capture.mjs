#!/usr/bin/env node
// 用 Playwright 打开路由器后台，录制登录 + 进「终端管理」时的所有真实 /api|/cgi/ 请求。
// 抓到真实端点/请求体后，接回 scripts/router_fetch.py 里做免浏览器抓取。
//
// 跑法（从 alva-e2e 目录，复用其 playwright 模块）：
//   cd work/playwright/alva-e2e
//   node /Users/.../work/lume/lume-lan/scripts/router_capture.mjs
//
// 交互式：脚本打开浏览器后，请手动 ①登录 admin ②进「终端管理/流量」页，然后回终端按回车结束。

import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { createRequire } from "node:module";

// 复用 work/playwright/alva-e2e 里已装好的 @playwright/test（免在 lume-lan 里再装依赖）
const requireFromAlva = createRequire(
  "/Users/erishen/Workspace/CNB/individular-invest/work/playwright/alva-e2e/package.json",
);
const { chromium } = requireFromAlva("@playwright/test");

// 读项目根（.mjs 所在目录的上一级）的 .env
const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const ENV_FILE = path.join(ROOT, ".env");

function loadEnv() {
  const map = {};
  try {
    const txt = fs.readFileSync(ENV_FILE, "utf8");
    for (const line of txt.split("\n")) {
      const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
      if (m) map[m[1]] = m[2];
    }
  } catch {}
  const host = (map.ROUTER_HOST || "http://192.168.1.1").replace(/^http:\/\//, "");
  return { host: `http://${host}`, user: map.ROUTER_USER || "admin", pass: map.ROUTER_PASS || "" };
}

const { host } = loadEnv();
const OUT = path.join(ROOT, "docs", "router-capture.json");

const calls = []; // {t, method, url, status, reqBody?, respSnippet?}
const cookieLog = [];

function apiMatch(u) {
  return /\/(api|cgi)\//i.test(u) || /(?:multiget|userlogin|usernonce|user_login_proof|slavedevinfo|getmacaddress|iot_)/i.test(u);
}

function dump(quiet = false) {
  const payload = {
    host,
    captured_at: new Date().toISOString(),
    cookies: cookieLog,
    calls,
  };
  fs.writeFileSync(OUT, JSON.stringify(payload, null, 2));
  if (quiet) return;
  console.log(`\n✅ 已写 ${calls.length} 条请求 -> ${OUT}`);
  console.log("   关键 /api 端点：");
  for (const c of calls) {
    const snippet = c.respSnippet ? c.respSnippet.slice(0, 120).replace(/\s+/g, " ") : "";
    console.log(`   [${c.method}] ${c.url}  -> ${c.status}  ${snippet}`);
  }
}

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
const waitEnter = () => new Promise((res) => rl.question("\n按回车结束录制…", () => res()));
// 录制时长（秒）。默认 180s；可用 CAPTURE_TIMEOUT 覆盖。期间每 10s 自动转储一次，
// 超时或按 Ctrl-C 都会落盘，便于后台运行 + 手动在浏览器操作。
const TIMEOUT_S = parseInt(process.env.CAPTURE_TIMEOUT || "180", 10);
const FLUSH_MS = 10000;

// Chrome 临时目录通过 TMPDIR 指到 workspace 可写目录，避免沙箱拦 /var/folders
const browser = await chromium.launch({
  headless: false,
  channel: "chrome",
  args: ["--no-first-run", "--no-default-browser-check"],
});
const context = await browser.newContext({
  ignoreHTTPSErrors: true,
  viewport: { width: 1280, height: 800 },
  userAgent:
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
});
// 让请求在响应头返回后能拿到 body
context.setExtraHTTPHeaders({});
const page = await context.newPage();

const seenBody = new WeakSet();
page.on("request", (req) => {
  const u = req.url();
  if (!apiMatch(u)) return;
  const entry = {
    t: Date.now(),
    method: req.method(),
    url: u,
    reqBody: (() => {
      try {
        const b = req.postData() || "";
        return b.slice(0, 4000);
      } catch {
        return "";
      }
    })(),
    headers: (() => {
      const h = { ...req.headers() };
      // 只留与鉴权/协议相关的，省体积
      const keep = {};
      for (const k of Object.keys(h)) {
        if (/^(authorization|cookie|x-|content-type|referer)/i.test(k)) keep[k] = h[k];
      }
      return keep;
    })(),
  };
  calls.push(entry);
});

page.on("response", async (res) => {
  const u = res.url();
  const entry = calls.find((c) => c.url === u && c.status == null);
  const target = entry || calls[calls.length - 1];
  if (target && target.status == null) target.status = res.status();
  if (!apiMatch(u)) return;
  try {
    const ct = res.headers()["content-type"] || "";
    let body = "";
    if (/json/i.test(ct)) body = await res.text();
    else {
      const buf = await res.body().catch(() => Buffer.alloc(0));
      body = buf.toString("utf8").slice(0, 4000);
    }
    // 把对应 call 的 respSnippet 补上
    const c = calls.find((x) => x.url === u && !x.respSnippet);
    if (c) c.respSnippet = body;
  } catch {}
});

page.on("requestfinished", async (req) => {
  try {
    const cookies = await context.cookies();
    const c = cookies.map((x) => `${x.name}=${x.value}`).join("; ");
    if (cookieLog[cookieLog.length - 1] !== c) cookieLog.push({ t: Date.now(), c });
  } catch {}
});

console.log(`▶ 打开 ${host} … 请手动：①登录 admin  ②进「终端管理 / 流量统计」页`);
try {
  await page.goto(host, { waitUntil: "domcontentloaded", timeout: 45000 });
} catch (e) {
  console.warn(`(goto ${e.message.split("\n")[0]}；继续录制中)`);
}

// 后台模式：按 TIMEOUT_S 定时自动结束，期间每 FLUSH_MS 自动落盘一次（方便你在浏览器里操作）。
let done = false;
const stopOnSigint = () => {
  if (done) return;
  done = true;
  console.log("\n⏎ Ctrl-C 触发，立即落盘…");
};
process.on("SIGINT", stopOnSigint);

const deadline = Date.now() + TIMEOUT_S * 1000;
while (!done && Date.now() < deadline) {
  await new Promise((r) => setTimeout(r, FLUSH_MS));
  dump(true); // 周期落盘，不打印全量，避免刷屏
  process.stdout.write(`  …已录 ${calls.length} 条 (剩 ${Math.ceil((deadline - Date.now()) / 1000)}s)\n`);
}
// 也可随时 Ctrl-C 提前结束
if (!done) done = true;

await dump();
// 存 storageState，供后续自动化复用
try {
  await context.storageState({ path: path.join(ROOT, ".data", "router-state.json") });
  console.log("   已存登录态 -> .data/router-state.json");
} catch {}

await browser.close();
rl.close();
