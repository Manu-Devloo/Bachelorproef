#!/usr/bin/env node
/* eslint-disable no-console */
const fs = require("node:fs/promises");
const path = require("node:path");
const { chromium } = require("playwright");

const VIEWPORT = { width: 1280, height: 720 };

function parseArgs(argv) {
  const args = {};
  for (let index = 2; index < argv.length; index += 1) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key.startsWith("--")) {
      continue;
    }
    args[key.slice(2)] = value;
    index += 1;
  }
  if (!args.config || !args.clip) {
    throw new Error("Usage: video_capture.cjs --config <state.json> --clip <name>");
  }
  return args;
}

function cardHtml(title, body, kicker = "CTFd Container Plugin PoC") {
  return `<!doctype html>
  <html lang="en">
    <head>
      <meta charset="utf-8" />
      <style>
        :root {
          color-scheme: light;
          font-family: "SF Pro Display", "Segoe UI", sans-serif;
        }
        body {
          margin: 0;
          width: 1280px;
          height: 720px;
          display: grid;
          place-items: center;
          background:
            radial-gradient(circle at top right, rgba(13, 110, 253, 0.18), transparent 40%),
            radial-gradient(circle at bottom left, rgba(32, 201, 151, 0.22), transparent 35%),
            linear-gradient(135deg, #0b1320 0%, #101a2f 45%, #182845 100%);
          color: #f8fafc;
        }
        .panel {
          width: 980px;
          padding: 48px 56px;
          border-radius: 28px;
          background: rgba(10, 17, 31, 0.78);
          border: 1px solid rgba(255, 255, 255, 0.08);
          box-shadow: 0 30px 70px rgba(0, 0, 0, 0.35);
        }
        .kicker {
          margin: 0 0 18px;
          font-size: 16px;
          letter-spacing: 0.18em;
          text-transform: uppercase;
          color: #7dd3fc;
        }
        h1 {
          margin: 0 0 18px;
          font-size: 50px;
          line-height: 1.05;
          letter-spacing: -0.03em;
        }
        p {
          margin: 0;
          font-size: 24px;
          line-height: 1.45;
          color: rgba(248, 250, 252, 0.86);
          white-space: pre-line;
        }
      </style>
    </head>
    <body>
      <section class="panel">
        <p class="kicker">${kicker}</p>
        <h1>${title}</h1>
        <p>${body}</p>
      </section>
    </body>
  </html>`;
}

async function sleep(page, ms) {
  await page.waitForTimeout(ms);
}

async function login(page, credentials) {
  await page.goto(`${state.base_url}/login`, { waitUntil: "domcontentloaded" });
  await page.fill('input[name="name"]', credentials.name || credentials.username);
  await sleep(page, 350);
  await page.fill('input[name="password"]', credentials.password);
  await sleep(page, 350);
  await page.click('input[type="submit"], button[type="submit"]');
  await page.waitForLoadState("domcontentloaded");
  await sleep(page, 1400);
}

async function openChallenge(page, challenge) {
  await page.goto(`${state.base_url}/challenges`, { waitUntil: "domcontentloaded" });
  await sleep(page, 1200);
  const challengeCard = page.locator(`button.challenge-button[value="${challenge.id}"]`).first();
  await challengeCard.waitFor({ state: "visible", timeout: 30000 });
  await challengeCard.click();
  await page.waitForSelector("#container-runtime-panel", { timeout: 30000 });
  await sleep(page, 1500);
}

async function waitForStatusIncludes(page, expected) {
  await page.waitForFunction(
    (needle) => {
      const node = document.getElementById("container-runtime-status");
      return node && node.textContent && node.textContent.toLowerCase().includes(needle.toLowerCase());
    },
    expected,
    { timeout: 60000 },
  );
}

async function recordClip(clipName, handler) {
  const browser = await chromium.launch({ headless: true, slowMo: 120 });
  const context = await browser.newContext({
    viewport: VIEWPORT,
    recordVideo: {
      dir: state.clips_dir,
      size: VIEWPORT,
    },
  });
  const page = await context.newPage();
  const video = page.video();
  try {
    await handler(page, context);
    await sleep(page, 800);
  } finally {
    await context.close();
    await browser.close();
  }
  const recordedPath = await video.path();
  const destination = path.join(state.clips_dir, `${clipName}.webm`);
  await fs.copyFile(recordedPath, destination);
}

async function clipIntro(page) {
  await page.setContent(
    cardHtml(
      "Autonomous Demo Run",
      "Per-player challenge containers inside CTFd.\nThis video shows the live runtime flow, controls, cleanup paths, and deployment proof.",
    ),
  );
  await sleep(page, 3600);
}

async function clipSection(page, title, body) {
  await page.setContent(cardHtml(title, body));
  await sleep(page, 2600);
}

async function clipHook(page) {
  await login(page, state.users.player1);
  await openChallenge(page, state.primary_challenge);
  await page.click("#container-runtime-start");
  await waitForStatusIncludes(page, "instance running");
  await sleep(page, 2200);
  const href = await page.getAttribute("#container-runtime-access a", "href");
  if (!href) {
    throw new Error("Expected runtime access URL after start");
  }
  await sleep(page, 1200);
  await page.goto(href, { waitUntil: "domcontentloaded" });
  await sleep(page, 2800);
}

async function clipAdminConfig(page) {
  await login(page, state.admin);
  await page.goto(`${state.base_url}/admin/challenges/new`, { waitUntil: "domcontentloaded" });
  await sleep(page, 1200);
  await page.locator('input[name="type"][value="containerized"]').evaluate((element) => {
    element.checked = true;
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await page.waitForSelector('#create-chal-entry-div form input[name="image"]', { timeout: 30000 });
  await sleep(page, 1200);
  await page.fill('#create-chal-entry-div input[name="name"]', "Video Demo Challenge");
  await sleep(page, 350);
  await page.fill('#create-chal-entry-div input[name="category"]', "Docker");
  await sleep(page, 350);
  await page.fill('#create-chal-entry-div input[name="value"]', "100");
  await sleep(page, 500);
  await page.setInputFiles('#create-chal-entry-div input[name="image_archive"]', state.archive_path);
  await page.waitForFunction(
    () => {
      const node = document.querySelector("#create-chal-entry-div [data-upload-status]");
      return node && node.textContent && node.textContent.includes("Uploaded");
    },
    { timeout: 60000 },
  );
  await sleep(page, 1200);
  await page.fill('#create-chal-entry-div input[name="container_ports"]', "8080");
  await sleep(page, 250);
  await page.fill('#create-chal-entry-div input[name="timeout_seconds"]', "120");
  await sleep(page, 250);
  await page.fill('#create-chal-entry-div input[name="cpu_limit"]', "0.5");
  await sleep(page, 250);
  await page.fill('#create-chal-entry-div input[name="memory_limit_mb"]', "256");
  await sleep(page, 250);
  await page.fill('#create-chal-entry-div input[name="max_instances"]', "2");
  await sleep(page, 1800);
  await page.mouse.wheel(0, 700);
  await sleep(page, 1400);
  await page.mouse.wheel(0, -350);
  await sleep(page, 1800);
}

async function clipPlayerReuse(page) {
  await login(page, state.users.player1);
  await openChallenge(page, state.primary_challenge);
  await waitForStatusIncludes(page, "instance running");
  await sleep(page, 1000);
  await page.click("#container-runtime-start");
  await waitForStatusIncludes(page, "instance running");
  await sleep(page, 3200);
}

async function clipPlayer2Start(page) {
  await login(page, state.users.player2);
  await openChallenge(page, state.primary_challenge);
  await page.click("#container-runtime-start");
  await waitForStatusIncludes(page, "instance running");
  await sleep(page, 3200);
}

async function clipPlayer3Capacity(page) {
  await login(page, state.users.player3);
  await openChallenge(page, state.primary_challenge);
  await page.click("#container-runtime-start");
  await page.waitForFunction(
    () => {
      const node = document.getElementById("container-runtime-status");
      return node && node.textContent && node.textContent.toLowerCase().includes("max_instances");
    },
    { timeout: 60000 },
  );
  await sleep(page, 3600);
}

async function clipPlayer1Solve(page) {
  await login(page, state.users.player1);
  await openChallenge(page, state.primary_challenge);
  await page.fill("#challenge-input", state.primary_challenge.flag);
  await sleep(page, 500);
  await page.click("#challenge-submit");
  await page.waitForFunction(
    () => {
      const node = document.getElementById("container-runtime-status");
      return node && node.textContent && node.textContent.toLowerCase().includes("solved the challenge");
    },
    { timeout: 60000 },
  );
  await sleep(page, 3200);
}

async function clipAdminForceStop(page) {
  await login(page, state.admin);
  await page.goto(`${state.base_url}/admin/plugins/containerized-challenges`, { waitUntil: "domcontentloaded" });
  await sleep(page, 1600);
  await page.mouse.wheel(0, 980);
  await sleep(page, 1300);
  const stopButton = page.locator('button:has-text("Force Stop"):not([disabled])').first();
  await stopButton.waitFor({ state: "visible", timeout: 30000 });
  await stopButton.click();
  await page.waitForLoadState("domcontentloaded");
  await sleep(page, 2600);
}

async function clipTimeoutStart(page) {
  await login(page, state.users.timeoutplayer);
  await openChallenge(page, state.timeout_challenge);
  await page.click("#container-runtime-start");
  await waitForStatusIncludes(page, "instance running");
  await sleep(page, 3200);
}

async function clipTimeoutResult(page) {
  await login(page, state.admin);
  await page.goto(`${state.base_url}/admin/plugins/containerized-challenges`, { waitUntil: "domcontentloaded" });
  await sleep(page, 1400);
  await page.mouse.wheel(0, 1150);
  await sleep(page, 1000);
  await page.waitForSelector("text=expired", { timeout: 30000 });
  await sleep(page, 3200);
}

async function clipAdminOverview(page) {
  await login(page, state.admin);
  await page.goto(`${state.base_url}/admin/plugins/containerized-challenges`, { waitUntil: "domcontentloaded" });
  await sleep(page, 1600);
  await page.click('button:has-text("Run Reaper Now")');
  await page.waitForLoadState("domcontentloaded");
  await sleep(page, 1600);
  await page.mouse.wheel(0, 700);
  await sleep(page, 1200);
  await page.mouse.wheel(0, 800);
  await sleep(page, 1200);
  await page.mouse.wheel(0, 900);
  await sleep(page, 1200);
  await page.mouse.wheel(0, 900);
  await sleep(page, 2200);
}

async function clipSplitHost(page) {
  await page.setContent(
    cardHtml(
      "Split-Host Support",
      "The same plugin can target a remote Docker host.\n\nKey runtime variables:\nCTFD_CONTAINER_DOCKER_HOST\nCTFD_CONTAINER_PUBLIC_HOST\nCTFD_CONTAINER_PUBLISHED_PORT_MIN / MAX\n\nThis was also validated on a real two-VM Lima setup.",
      "Deployment Proof",
    ),
  );
  await sleep(page, 6200);
}

async function clipOutro(page) {
  await page.setContent(
    cardHtml(
      "Validated End To End",
      "Custom challenge type, archive upload, isolated runtimes, idempotent reuse, capacity limits, solve cleanup, timeout expiry, and admin observability are all covered in this automated run.",
    ),
  );
  await sleep(page, 4200);
}

const args = parseArgs(process.argv);
const state = JSON.parse(require("node:fs").readFileSync(args.config, "utf8"));

const clipHandlers = {
  intro: clipIntro,
  admin_config_title: (page) =>
    clipSection(page, "Admin Configuration", "The plugin adds a custom challenge type with archive-backed image upload and per-challenge runtime limits."),
  reuse_title: (page) =>
    clipSection(page, "Runtime Reuse And Capacity", "A repeated start for the same player reuses the active container.\nAdditional players get their own instance until max_instances is reached."),
  cleanup_title: (page) =>
    clipSection(page, "Cleanup And Operations", "Correct solves stop the player runtime automatically.\nAdmins can force-stop instances, inspect runtime history, and verify timeout expiry."),
  hook: clipHook,
  admin_config: clipAdminConfig,
  player_reuse: clipPlayerReuse,
  player2_start: clipPlayer2Start,
  player3_capacity: clipPlayer3Capacity,
  player1_solve: clipPlayer1Solve,
  admin_force_stop: clipAdminForceStop,
  timeout_start: clipTimeoutStart,
  timeout_result: clipTimeoutResult,
  admin_overview: clipAdminOverview,
  split_host: clipSplitHost,
  outro: clipOutro,
};

const handler = clipHandlers[args.clip];
if (!handler) {
  throw new Error(`Unknown clip '${args.clip}'`);
}

recordClip(args.clip, handler).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
