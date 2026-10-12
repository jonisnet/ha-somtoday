import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { access, cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

class ExtensionLoadUnavailableError extends Error {}

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const helperDirectory = path.resolve(scriptDirectory, "..");
const callback =
  "somtoday://nl.topicus.somtoday.leerling/oauth/callback?code=browser-smoke-code";

const browserCandidates = [
  {
    name: "Chrome",
    paths: [
      path.join(process.env.PROGRAMFILES ?? "", "Google/Chrome/Application/chrome.exe"),
      path.join(process.env["PROGRAMFILES(X86)"] ?? "", "Google/Chrome/Application/chrome.exe"),
      path.join(process.env.LOCALAPPDATA ?? "", "Google/Chrome/Application/chrome.exe"),
    ],
  },
  {
    name: "Edge",
    paths: [
      path.join(process.env["PROGRAMFILES(X86)"] ?? "", "Microsoft/Edge/Application/msedge.exe"),
      path.join(process.env.PROGRAMFILES ?? "", "Microsoft/Edge/Application/msedge.exe"),
      path.join(process.env.LOCALAPPDATA ?? "", "Microsoft/Edge/Application/msedge.exe"),
    ],
  },
];

const server = http.createServer((_request, response) => {
  response.writeHead(302, { Location: callback });
  response.end();
});
server.listen(0, "127.0.0.1");
await once(server, "listening");
const { port } = server.address();

const results = [];
try {
  for (const candidate of browserCandidates) {
    const executable = await firstExisting(candidate.paths);
    if (!executable) {
      results.push(`${candidate.name}: skipped (not installed)`);
      continue;
    }
    console.log(`${candidate.name}: testing ${executable}`);
    try {
      await testBrowser(candidate.name, executable, port);
      results.push(`${candidate.name}: passed`);
    } catch (error) {
      const status = error instanceof ExtensionLoadUnavailableError ? "skipped" : "failed";
      results.push(`${candidate.name}: ${status} (${error.message})`);
    }
  }
} finally {
  server.close();
}

console.log(results.join("\n"));
assert.ok(results.some((result) => result.endsWith("passed")), "No browser smoke test passed");
assert.deepEqual(
  results.filter((result) => result.includes(": failed")),
  [],
  "One or more browser smoke tests failed",
);

async function testBrowser(name, executable, testPort) {
  const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "somtoday-helper-smoke-"));
  const extensionDirectory = path.join(temporaryRoot, "extension");
  const profileDirectory = path.join(temporaryRoot, "profile");
  let browser;
  let cdp;

  try {
    await cp(helperDirectory, extensionDirectory, {
      recursive: true,
      filter(source) {
        return !source.includes(`${path.sep}node_modules${path.sep}`);
      },
    });
    await prepareLocalTestExtension(extensionDirectory);

    browser = spawn(
      executable,
      [
        `--user-data-dir=${profileDirectory}`,
        `--disable-extensions-except=${extensionDirectory}`,
        `--load-extension=${extensionDirectory}`,
        "--remote-debugging-port=0",
        "--remote-allow-origins=*",
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=msEdgeFirstRunExperience",
        "about:blank",
      ],
      { stdio: "ignore" },
    );

    const debuggerAddress = await waitForDevTools(profileDirectory);
    cdp = await connectCdp(debuggerAddress);
    try {
      await waitForTarget(
        cdp,
        (item) => item.type === "service_worker" && item.url.endsWith("/background.js"),
        `${name} did not load the temporary extension`,
      );
    } catch (error) {
      throw new ExtensionLoadUnavailableError(error.message);
    }
    await cdp.send("Target.createTarget", {
      url: `http://127.0.0.1:${testPort}/oauth-finish`,
    });
    const target = await waitForTarget(
      cdp,
      (item) => item.type === "page" && item.url.endsWith("/popup.html"),
      `${name} did not open the extension result page`,
    );
    const page = await cdp.session(target.targetId);
    const value = await waitForPopupValue(page);
    assert.equal(value.redirect, callback, `${name} did not expose the captured redirect`);
    assert.match(value.text, /Inlogadres gevonden/, `${name} did not show success`);
  } finally {
    if (cdp) {
      try {
        await withTimeout(cdp.send("Browser.close"), 2_000, "Browser close timed out");
      } catch {
        // The browser may already have exited after a failed assertion.
      }
      cdp.close();
    }
    if (browser && browser.exitCode === null) {
      browser.kill();
      await Promise.race([once(browser, "exit"), delay(2_000)]);
    }
    await rm(temporaryRoot, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
}

async function waitForPopupValue(page) {
  let value = {};
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const evaluation = await page.send("Runtime.evaluate", {
      expression:
        "({text: document.body.innerText, redirect: document.querySelector('#redirect')?.value})",
      returnByValue: true,
    });
    value = evaluation.result.value ?? {};
    if (value.redirect) return value;
    await delay(100);
  }
  return value;
}

async function prepareLocalTestExtension(directory) {
  const manifestPath = path.join(directory, "manifest.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  manifest.host_permissions = ["http://127.0.0.1/*"];
  await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);

  const backgroundPath = path.join(directory, "background.js");
  const background = await readFile(backgroundPath, "utf8");
  await writeFile(
    backgroundPath,
    background.replace("https://inloggen.somtoday.nl/*", "http://127.0.0.1/*"),
  );
}

async function firstExisting(paths) {
  for (const candidate of paths) {
    try {
      await access(candidate);
      return candidate;
    } catch {
      // Try the next standard installation location.
    }
  }
  return null;
}

async function waitForDevTools(profileDirectory) {
  const file = path.join(profileDirectory, "DevToolsActivePort");
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const [port, browserPath] = (await readFile(file, "utf8")).trim().split(/\r?\n/);
      if (port && browserPath) {
        return `ws://127.0.0.1:${port}${browserPath}`;
      }
    } catch {
      // Browser startup is still in progress.
    }
    await delay(100);
  }
  throw new Error("Browser did not expose its debugging endpoint");
}

async function connectCdp(address) {
  const socket = new WebSocket(address);
  await withTimeout(once(socket, "open"), 5_000, "Could not connect to browser debugging endpoint");
  let nextId = 0;
  const pending = new Map();

  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message));
    else resolve(message.result);
  });

  function send(method, params = {}, sessionId) {
    const id = ++nextId;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  }

  return {
    close: () => socket.close(),
    send,
    async session(targetId) {
      const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
      return { send: (method, params = {}) => send(method, params, sessionId) };
    },
  };
}

async function waitForTarget(cdp, predicate, message = "Target did not appear") {
  let lastTargetInfos = [];
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const { targetInfos } = await cdp.send("Target.getTargets");
    lastTargetInfos = targetInfos;
    const target = targetInfos.find(predicate);
    if (target) return target;
    await delay(100);
  }
  const summary = lastTargetInfos.map(({ type, title, url }) => ({ type, title, url }));
  throw new Error(`${message}. Targets: ${JSON.stringify(summary)}`);
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function withTimeout(promise, milliseconds, message) {
  let timeout;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timeout = setTimeout(() => reject(new Error(message)), milliseconds);
      }),
    ]);
  } finally {
    clearTimeout(timeout);
  }
}
