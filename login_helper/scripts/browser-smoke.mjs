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
  "somtoday://nl.topicus.somtoday.leerling:443/oauth/callback?code=browser-smoke-code";

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

const server = http.createServer((request, response) => {
  if (request.url === "/login") {
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    response.end(
      "<!doctype html><title>Login simulation</title>" +
        "<script>location.href='/oauth-finish';</script>",
    );
    return;
  }

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

  try {
    console.log(`${name}: preparing temporary extension`);
    await cp(helperDirectory, extensionDirectory, {
      recursive: true,
      filter(source) {
        return !source.includes(`${path.sep}node_modules${path.sep}`);
      },
    });
    await prepareLocalTestExtension(extensionDirectory);
    console.log(`${name}: temporary extension prepared`);

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
    console.log(`${name}: browser process started`);

    const debuggerPort = await waitForDevTools(profileDirectory);
    console.log(`${name}: debugging endpoint ready`);
    let extensionTarget;
    try {
      extensionTarget = await waitForTarget(
        debuggerPort,
        (item) => item.type === "service_worker" && item.url.endsWith("/background.js"),
        `${name} did not load the temporary extension`,
      );
    } catch (error) {
      throw new ExtensionLoadUnavailableError(error.message);
    }
    console.log(`${name}: extension service worker loaded`);
    await openTarget(debuggerPort, `http://127.0.0.1:${testPort}/login`);
    console.log(`${name}: local login simulation opened`);
    let target;
    try {
      target = await waitForTarget(
        debuggerPort,
        (item) => item.type === "page" && item.url.endsWith("/popup.html"),
        `${name} did not open the extension result page`,
      );
      console.log(`${name}: extension result page opened`);
    } catch (error) {
      throw new Error(`${error.message}. Worker: ${extensionTarget.url}`);
    }
    assert.match(target.title, /Somtoday Login Helper/i);
  } finally {
    if (browser && browser.exitCode === null) {
      browser.kill();
      await Promise.race([once(browser, "exit"), delay(2_000)]);
    }
    await rm(temporaryRoot, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
}

async function prepareLocalTestExtension(directory) {
  const manifestPath = path.join(directory, "manifest.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  manifest.host_permissions = ["http://127.0.0.1/*"];
  await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);

  const backgroundPath = path.join(directory, "background.js");
  const background = await readFile(backgroundPath, "utf8");
  const patchedBackground = background.replace(
    "https://inloggen.somtoday.nl/*",
    "http://127.0.0.1/*",
  );
  const diagnosticListeners = `
chrome.webRequest.onHeadersReceived.addListener(
  (details) => void chrome.storage.session.set({
    smokeHeadersEvent: {
      statusCode: details.statusCode,
      type: details.type,
      headers: (details.responseHeaders ?? []).map(({ name, value }) => ({ name, value })),
    },
  }),
  { urls: ["http://127.0.0.1/*"] },
  ["responseHeaders", "extraHeaders"],
);
chrome.webRequest.onBeforeRedirect.addListener(
  (details) => void chrome.storage.session.set({
    smokeRedirectEvent: {
      redirectUrl: details.redirectUrl,
      statusCode: details.statusCode,
      type: details.type,
    },
  }),
  { urls: ["http://127.0.0.1/*"] },
);
chrome.webRequest.onErrorOccurred.addListener(
  (details) => void chrome.storage.session.set({
    smokeErrorEvent: {
      error: details.error,
      type: details.type,
      url: details.url,
    },
  }),
  { urls: ["http://127.0.0.1/*"] },
);
`;
  await writeFile(backgroundPath, `${patchedBackground}\n${diagnosticListeners}`);
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
        return Number(port);
      }
    } catch {
      // Browser startup is still in progress.
    }
    await delay(100);
  }
  throw new Error("Browser did not expose its debugging endpoint");
}

async function openTarget(debuggerPort, url) {
  const response = await fetch(
    `http://127.0.0.1:${debuggerPort}/json/new?${encodeURIComponent(url)}`,
    { method: "PUT" },
  );
  if (!response.ok) {
    throw new Error(`Browser could not open the local login simulation (${response.status})`);
  }
}

async function waitForTarget(debuggerPort, predicate, message = "Target did not appear") {
  let lastTargetInfos = [];
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const response = await fetch(`http://127.0.0.1:${debuggerPort}/json/list`);
    if (!response.ok) {
      throw new Error(`Browser did not return its target list (${response.status})`);
    }
    const targetInfos = await response.json();
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
