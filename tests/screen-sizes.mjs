// Screen-size check: renders every main screen at every supported panel size
// and lane count against dev-server.py, and fails if anything spills off the
// screen or out of its panel, or if the page logs an error.
//
//     npm run test:screens
//
// The supported sizes are listed in README.md (Display). Add a size here
// before promising it to anyone. To try a panel first without editing this
// file:  SIZES=1366x768,1280x1024 npm run test:screens
// Chromium comes from Playwright; to use one already on the machine, set
// CHROMIUM_PATH to its executable.

import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { chromium } from "playwright";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// Landscape, 800x480 and up: the supported range.
const DEFAULT_SIZES = [
  [800, 480],   // the design canvas
  [1024, 600],  // Waveshare 7" HDMI LCD (C), the production panel
  [1280, 800],  // common 10.1"
  [1280, 720],  // 16:9
  [1024, 768],  // 4:3
  [1920, 1080], // 16:9 large
];
const SIZES = process.env.SIZES
  ? process.env.SIZES.split(",").map((t) => t.trim().split("x").map(Number))
  : DEFAULT_SIZES;
const LANE_COUNTS = [1, 6, 15];

let port = 8900;

async function startBackend(lanes) {
  const p = port++;
  const consoles = lanes + 3 > 16 ? 16 - lanes : 3;
  const proc = spawn("python3", ["dev-server.py", String(p), "--lanes", String(lanes),
    "--consoles", String(consoles)], { cwd: ROOT, stdio: "ignore" });
  const base = `http://127.0.0.1:${p}`;
  for (let i = 0; i < 50; i++) {
    try { if ((await fetch(base + "/api/status")).ok) return { proc, base }; } catch { /* starting */ }
    await sleep(100);
  }
  proc.kill();
  throw new Error("dev-server.py did not start");
}

async function sim(base, name, body) {
  const r = await fetch(`${base}/api/sim/${name}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`sim ${name}: ${r.status} ${await r.text()}`);
}

const settle = (page) => page.waitForTimeout(1300);
const view = (page) => page.evaluate(() => Alpine.$data(document.body).view);

// Everything visible must be inside the screen, and every control inside the
// <main> it belongs to, unless it sits in a scrolling panel.
async function layoutProblems(page) {
  return page.evaluate(() => {
    const out = [];
    const W = window.innerWidth, H = window.innerHeight;
    const describe = (el) => el.getAttribute("aria-label") || el.dataset.testid ||
      (el.textContent || "").trim().slice(0, 30) || el.tagName;
    document.querySelectorAll("header, footer, main").forEach((box) => {
      if (getComputedStyle(box).display === "none") return;
      const r = box.getBoundingClientRect();
      if (r.left < -0.5 || r.top < -0.5 || r.right > W + 0.5 || r.bottom > H + 0.5) {
        out.push(`${box.tagName.toLowerCase()} runs off the screen`);
      }
      box.querySelectorAll("button, .call-light, input, textarea").forEach((el) => {
        const e = el.getBoundingClientRect();
        if (!e.width || !e.height || el.closest(".overflow-y-auto")) return;
        if (e.left < r.left - 0.5 || e.top < r.top - 0.5 ||
            e.right > r.right + 0.5 || e.bottom > r.bottom + 0.5) {
          out.push(`"${describe(el)}" spills out of ${box.tagName.toLowerCase()}`);
        }
      });
    });
    return out;
  });
}

async function checkSize(browser, base, lanes, [w, h]) {
  const page = await browser.newPage({ viewport: { width: w, height: h } });
  const problems = [];
  page.on("pageerror", (e) => problems.push(`page error: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console error: ${m.text()}`); });
  const check = async (label) => {
    for (const p of await layoutProblems(page)) problems.push(`${label}: ${p}`);
  };

  await sim(base, "reset", {});
  await page.goto(base + "/");
  await settle(page);

  // Main, with every call state on show.
  const states = [["lane-2", "ringing"], ["lane-3", "busy"], ["lane-4", "held"]];
  for (const [lane, state] of states) await sim(base, "lane", { lane, state }).catch(() => {});
  await sim(base, "lane", {
    lane: "lane-5", codes: [{ code: "A2B-DOWNSTREAM", severity: "error", text: "No signal" }],
  }).catch(() => {});
  await settle(page);
  await check("main");

  // Active call, by tapping the ringing lane (lane 1 when there is only one).
  const ringing = lanes > 1 ? 2 : 1;
  if (lanes === 1) await sim(base, "lane", { lane: "lane-1", state: "ringing" });
  await settle(page);
  const key = page.locator(`[data-lane='${ringing}']`);
  const box = await key.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.up();
  await settle(page);
  if ((await view(page)) !== "call") problems.push("tapping a ringing lane did not open the call");
  await check("active call");
  await page.evaluate(() => Alpine.$data(document.body).hangUp());
  await settle(page);

  for (const screen of ["troubleshooting", "help", "settings"]) {
    await page.evaluate((s) => Alpine.$data(document.body).go(s), screen);
    await settle(page);
    await check(screen);
  }
  for (const tab of ["consoles", "users", "site", "backup"]) {
    await page.evaluate((t) => { Alpine.$data(document.body).settingsTab = t; }, tab);
    await page.waitForTimeout(200);
    await check(`settings/${tab}`);
  }
  await page.evaluate(() => Alpine.$data(document.body).go("main"));

  // Locked, with a lane ringing and answer-without-PIN on.
  await sim(base, "features", { answer_without_pin: true });
  await sim(base, "login", { required: true, user: null });
  await sim(base, "lane", { lane: "lane-1", state: "ringing" });
  await settle(page);
  if ((await view(page)) !== "locked") problems.push("did not show the Locked screen");
  await check("locked");

  await page.close();
  return problems;
}

const browser = await chromium.launch(
  process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
let failed = 0;
try {
  for (const lanes of LANE_COUNTS) {
    const { proc, base } = await startBackend(lanes);
    try {
      for (const size of SIZES) {
        const problems = await checkSize(browser, base, lanes, size);
        const label = `${size[0]}x${size[1]}, ${lanes} lane${lanes === 1 ? "" : "s"}`;
        if (problems.length) {
          failed++;
          console.log(`FAIL  ${label}`);
          for (const p of [...new Set(problems)]) console.log(`        ${p}`);
        } else {
          console.log(`ok    ${label}`);
        }
      }
    } finally {
      proc.kill();
    }
  }
} finally {
  await browser.close();
}
console.log(failed ? `\n${failed} size/lane combinations failed` : "\nall sizes pass");
process.exit(failed ? 1 : 0);
