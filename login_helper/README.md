# Somtoday Login Helper

This folder is a self-contained Manifest V3 browser extension for Chrome and
Edge. It is an optional companion to the Home Assistant integration: it makes
the `somtoday://` OAuth redirect visible without asking users to search Chrome
DevTools. It never signs in, exchanges a code or talks to Home Assistant.

The extension is not published in a browser store. The existing DevTools
instructions remain the supported fallback.

## Easiest installation on Windows

1. Download the ready-made
   **[Somtoday Login Helper ZIP](../downloads/Somtoday-Login-Helper-0.1.1.zip)**.
2. Right-click the ZIP, choose **Extract all**, and open the extracted folder.
3. Double-click **`INSTALLEREN-WINDOWS.cmd`** and choose Chrome or Edge.
4. Enable **Developer mode**, choose **Load unpacked**, and select the folder
   the installer opened for you.
5. Pin **Somtoday Login Helper** from the browser's extensions menu.

`LEESMIJ-EERST.txt` in the ZIP contains the same instructions in Dutch. The
installer does not install software or change browser settings: it copies the
folder path and opens the two pages you need.

### Manual installation

If you do not want to run the Windows helper, extract the ZIP, open
`chrome://extensions` in Chrome or `edge://extensions` in Edge, enable
**Developer mode**, choose **Load unpacked**, and select the extracted folder
containing `manifest.json`.

Unpacked extensions do not update automatically. Download and extract the new
ZIP, then load its folder or choose **Reload** on the browser's extensions
page.

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
