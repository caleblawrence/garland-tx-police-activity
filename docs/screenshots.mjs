// Regenerate the README screenshots.
//
//   cd incident-geo-analysis && npm run build && npm run serve
//   node docs/screenshots.mjs http://127.0.0.1:8080 docs/img
//
// Drives the installed Chrome over the DevTools Protocol. No dependencies —
// Node 18+ has a global fetch and Node 22 has a global WebSocket. Output is
// PNG; the map-heavy shots are converted to JPEG afterwards because map tiles
// compress badly as PNG:
//
//   sips -s format jpeg -s formatOptions 82 docs/img/map-overview.png \
//        --out docs/img/map-overview.jpg
import { spawn } from "node:child_process";
import { writeFileSync, mkdirSync } from "node:fs";
import { setTimeout as sleep } from "node:timers/promises";

const CHROME =
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9337;
const BASE = process.argv[2] || "http://127.0.0.1:8080";
const OUT = process.argv[3] || "docs/img";

mkdirSync(OUT, { recursive: true });

const chrome = spawn(CHROME, [
  "--headless=new",
  `--remote-debugging-port=${PORT}`,
  "--hide-scrollbars",
  "--no-first-run",
  "--no-default-browser-check",
  "--user-data-dir=/tmp/claude-chrome-shots",
  "about:blank",
]);
chrome.stderr.on("data", () => {});

const waitForChrome = async () => {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (r.ok) return;
    } catch {}
    await sleep(250);
  }
  throw new Error("Chrome did not expose the debugging port");
};

class Session {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    ws.addEventListener("message", (e) => {
      const msg = JSON.parse(e.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) =>
      this.pending.set(id, { resolve, reject })
    );
  }
  async evaluate(expression) {
    const r = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    return r.result?.value;
  }
  async shot(file, { width, height, scale = 2 }) {
    await this.send("Emulation.setDeviceMetricsOverride", {
      width,
      height,
      deviceScaleFactor: scale,
      mobile: false,
    });
    await sleep(400);
    const { data } = await this.send("Page.captureScreenshot", {
      format: "png",
      captureBeyondViewport: false,
    });
    writeFileSync(`${OUT}/${file}`, Buffer.from(data, "base64"));
    console.log(`  wrote ${OUT}/${file} (${width}x${height} @${scale}x)`);
  }
  async go(url, settleMs = 3500) {
    await this.send("Page.navigate", { url });
    await sleep(settleMs);
  }
}

const open = async (width, height) => {
  const r = await fetch(
    `http://127.0.0.1:${PORT}/json/new?about:blank`,
    { method: "PUT" }
  );
  const target = await r.json();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener("open", res, { once: true });
    ws.addEventListener("error", rej, { once: true });
  });
  const s = new Session(ws);
  await s.send("Page.enable");
  await s.send("Runtime.enable");
  await s.send("Emulation.setDeviceMetricsOverride", {
    width,
    height,
    deviceScaleFactor: 2,
    mobile: false,
  });
  return s;
};

try {
  await waitForChrome();

  // 1. The incident browser — the front page
  console.log("incidents…");
  const browse = await open(1440, 1000);
  await browse.go(`${BASE}/index.html`, 4000);
  await browse.shot("incidents.png", { width: 1440, height: 1000 });

  // 2. A category selected: summary, offence breakdown, district choropleth
  console.log("category view…");
  await browse.evaluate(`(() => {
    const pill = [...document.querySelectorAll('.pill')]
      .find(b => /burglary/i.test(b.textContent));
    pill.click();
    document.getElementById('insight').scrollIntoView({ block: 'start' });
    return true;
  })()`);
  await sleep(1200);
  await browse.shot("category.png", { width: 1440, height: 1000 });

  // 3. Map overview — now a secondary page
  console.log("map overview…");
  const map = await open(1440, 900);
  await map.go(`${BASE}/map.html`, 5000);
  // fitBounds frames the data against a wide viewport, which over-fits the
  // width and leaves the boxes as specks. Tighten onto the city core.
  await map.evaluate(`(map.setZoom(map.getZoom() + 2), map.getZoom())`);
  await sleep(2500);
  await map.shot("map-overview.png", { width: 1440, height: 900 });

  // 4. Incident list expanded
  console.log("incident list…");
  await map.evaluate(`(document.getElementById('all-header').click(), true)`);
  await sleep(600);
  await map.shot("incident-list.png", { width: 1440, height: 900 });

  // 5. A single incident, zoomed in with its popup open
  console.log("incident detail…");
  const picked = await map.evaluate(`(async () => {
    const items = await (await fetch('incidents.json?x=' + Math.random())).json();
    // A recognisable offence with a box, so the popup reads well.
    const pick = items.find(i => i.status === 'mapped' && /Robbery|Burglary|Assault/i.test(i.short_description))
              || items.find(i => i.status === 'mapped');
    focusIncident(pick.id);
    return pick.short_description + ' @ ' + pick.location;
  })()`);
  console.log(`  focused: ${picked}`);
  await sleep(2500);
  await map.shot("incident-detail.png", { width: 1440, height: 900 });

  // 6. About page
  console.log("about page…");
  const about = await open(1440, 1000);
  await about.go(`${BASE}/about.html`, 3000);
  await about.shot("about.png", { width: 1440, height: 1000 });

  // 7. Mobile, for the layout note
  console.log("mobile…");
  const mob = await open(390, 780);
  await mob.go(`${BASE}/index.html`, 4000);
  await sleep(600);
  await mob.shot("mobile.png", { width: 390, height: 780, scale: 3 });

  console.log("done");
} finally {
  chrome.kill();
}
