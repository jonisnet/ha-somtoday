# Somtoday Login Helper

This folder is a self-contained Manifest V3 browser extension for Chrome and
Edge. It is an optional companion to the Home Assistant integration: it makes
the `somtoday://` OAuth redirect visible without asking users to search Chrome
DevTools. It never signs in, exchanges a code or talks to Home Assistant.

The extension is currently a technical feasibility release and is not
published in a browser store. The existing DevTools instructions remain the
supported fallback.

## Install from this repository

1. Download or clone this repository and keep the `login_helper` folder.
2. Open `chrome://extensions` in Chrome, or `edge://extensions` in Edge.
3. Enable **Developer mode**.
4. Choose **Load unpacked** and select the `login_helper` folder (the folder
   containing `manifest.json`).
5. Pin **Somtoday Login Helper** from the browser's extensions menu.

Unpacked extensions do not update automatically. Pull or download the new
repository version, then choose **Reload** on the browser's extensions page.

## Use it

1. Start adding or reauthenticating the Somtoday integration in Home Assistant.
2. Open the Somtoday login link from the Home Assistant form and sign in.
3. When Chrome discards the final `somtoday://` redirect, the helper shows a
   green `1` badge.
4. Open the helper and choose **Kopiëren en wissen**.
5. Paste the address into the Home Assistant form immediately.

The authorization code is single-use and short-lived. If no badge appears,
use the DevTools fallback in the repository's main README.

## Development and tests

The security-sensitive URL recognition lives in `oauth_redirect.js` and has no
browser dependencies. Run its tests with Node.js 20 or newer:

```console
cd login_helper
npm test
```

No package installation or network access is required.

See [SECURITY.md](SECURITY.md) for the threat model, permission rationale and
known limitations.
