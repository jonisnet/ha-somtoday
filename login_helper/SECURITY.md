# Security design

The redirect contains a short-lived, single-use authorization code. Treat it
like a password until Home Assistant has exchanged it.

## What the extension can access

The Manifest V3 permissions are deliberately narrow:

- `host_permissions` contains only `https://inloggen.somtoday.nl/*`.
- `webRequest` reads response headers for top-level navigations on that host.
  It does not block, alter or redirect traffic and does not request the MV2
  `webRequestBlocking` capability.
- `storage` keeps one validated redirect in `chrome.storage.session`, which is
  memory-only and explicitly limited to trusted extension contexts.
- `alarms` deletes that redirect after five minutes even when the MV3 service
  worker has stopped.

There are no content scripts, analytics, remote scripts, external requests or
automatic submission to Home Assistant. Clipboard access is attempted only
after the user presses the copy button; the extension requests no clipboard
permission and falls back to selecting the text for manual copying.

## Validation boundary

A response is retained only when all of these conditions hold:

1. the response belongs to a top-level request on
   `https://inloggen.somtoday.nl/*`;
2. its `Location` header is a valid URL with the exact scheme, host and path
   `somtoday://nl.topicus.somtoday.leerling/oauth/callback`;
3. it contains exactly one non-empty `code` and one non-empty `state` query
   parameter, no credentials and no fragment.

The helper preserves the callback unchanged. Home Assistant remains the OAuth
relying party and performs the authoritative comparison between the returned
`state` and the state from the PKCE flow it created. The extension never sees
the code verifier and cannot exchange the code itself.

## Lifetime and deletion

The validated redirect is held in memory-only session storage for at most five
minutes. It is removed sooner after a successful copy or when the user presses
**Wissen**. It is never logged, synced or written to normal local storage.

## Known limitations

- This feasibility release is installed unpacked and has not gone through a
  Chrome Web Store or Microsoft Edge Add-ons review.
- It assumes Somtoday returns the final custom-scheme redirect from
  `inloggen.somtoday.nl` and echoes the OAuth `state`, as requested by the
  integration. If that server behaviour changes, the helper fails closed and
  the DevTools method still works.
- The extension cannot paste into Home Assistant automatically without asking
  for much broader access. The user deliberately remains in control of that
  hand-off.

Report suspected vulnerabilities privately to the repository owner. Do not
attach a real redirect, authorization code, token or student data to a public
issue.
