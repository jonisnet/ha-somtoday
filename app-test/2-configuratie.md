## Configuration

For Chrome and Edge, download the optional
**[Somtoday Login Helper 0.1.4 (ZIP)](https://github.com/jonisnet/ha-somtoday/releases/download/v0.1.8/Somtoday-Login-Helper-0.1.4.zip)**.
Extract it and double-click **`INSTALLEREN-WINDOWS.cmd`**; it opens the browser
page and folder you need. The helper detects only this exact Somtoday callback
and gives you a **Kopiëren en wissen** button. It uses Manifest V3 and does not
change or couple itself to the Home Assistant integration. The
[full guide](login_helper/README.md) covers manual installation, usage,
permissions and security. The DevTools method below remains available without
an extension.

Go to **Settings → Devices & services → Add integration** and pick **Somtoday**.

The form shows a link and asks for one thing back:

1. **Open the login link** in a new browser tab. This is Somtoday's own login
   page: pick the school, then sign in as usual.
2. Somtoday redirects to an address starting with `somtoday://`. No browser can
   open that — and **that address carries the login code**.
3. **Paste it into Home Assistant.**

### If nothing seems to happen after signing in

That is Chrome, and **your login did work**. Chrome silently discards redirects
to schemes it has no handler for: no error page, no address-bar change, nothing.
Older browsers showed an "unknown protocol" error; Chrome does not. Pressing
sign-in again just restarts the whole login, which is why it asks for your
password a second time.

The code is still there, in the redirect Chrome threw away. To read it out:

1. **Before** signing in, press <kbd>F12</kbd> to open DevTools.
2. Go to the **Network** tab and tick **Preserve log**.
3. Sign in. Nothing visible happens — that is the point.
4. Click anywhere in the request list and press <kbd>Ctrl</kbd>+<kbd>F</kbd>,
   then search for `somtoday://`. DevTools searches inside headers too, so this
   lands directly on the one response that carries the code.
5. Open **Headers → Response Headers** on that request and copy the `location:`
   value. It starts with `somtoday://` and contains `code=`.
6. Paste it into Home Assistant. **Pasting the whole `location:` line is fine** —
   the field looks for the code anywhere in what you paste, so surrounding
   headers or quotes do not matter.

> Search rather than scroll. Chrome appends new requests to the **bottom** of
> the list, so the request you need is the last one. The *first* entry is the
> original authorization request, and its `location` points back at the login
> page — copying that one is the easiest mistake to make here.

This is not a workaround for a bug on our side. Somtoday's OAuth client only
accepts its own `somtoday://` redirect address — every alternative is rejected
with HTTP 400 — so there is no redirect a browser could display instead.

### About the code

It is single-use and expires within a few minutes. If Somtoday *rejects* it,
the form mints a new link and you do have to sign in again. If the paste was
simply the wrong text, the link you already opened stays valid — no second
login needed.

To deliberately replace a stored Somtoday session, open the integration's
menu and choose **Reconfigure**. This runs the same login flow without deleting
the integration, its entities or its local planner data. A rejected refresh
token also opens this flow automatically.

Access tokens expire after about one hour. The integration refreshes them in
the background and immediately stores Somtoday's rotated refresh token, so a
normal expiry does not require another manual login.

### Why this dance?

Somtoday's login is designed for its own mobile app, which registers the
`somtoday://` scheme on the phone. Home Assistant is not that app and cannot
receive the redirect, so the round trip goes through your browser instead.

The upside is real: because you sign in on Somtoday's actual login page, this
works for **every** school — including the ones behind Microsoft or Google SSO,
and ones with two-factor authentication — and **Home Assistant never sees your
password**. It only ever holds a refresh token, which you can revoke by changing
your Somtoday password.

### Supported authentication

| Method | Supported | Why |
|---|---|---|
| Authorization code + PKCE (the flow above) | ✅ | Works for every school, no password in Home Assistant |
| School SSO (Microsoft, Google, school portal) | ✅ | You sign in on Somtoday's page, so SSO is handled there |
| Two-factor authentication | ✅ | Same reason |
| Automatic token refresh | ✅ | Refresh tokens rotate and are re-persisted each poll |
| Re-authentication when the session dies | ✅ | Home Assistant prompts with the same paste flow |
| Username + password entered in Home Assistant | ❌ | Somtoday disabled the password grant: it answers `Password grant is disabled for insecure clients`. The remaining option is scraping Somtoday's login form, which breaks on every UI change and fails outright on SSO schools. Deliberately not implemented. |

### Options

**Settings → Devices & services → Somtoday → Configure**:

- **Refresh interval** — 15 minutes to 4 hours, default 30. A timetable rarely
  changes faster than that.
- **Days of schedule to fetch** — 1, 2, 3, 4 or 8 weeks, default 8. The integration
  always keeps eight weeks available for compatible dashboard cards. This is
  what the sensors and the cached calendar cover; the calendar can still show
  any period, it just fetches those on demand.

