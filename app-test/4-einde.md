## Examples

Ready-made automations live in [`examples/`](examples/) — an evening-before
summary, a reminder before the first lesson, a cancelled-lesson notification
and a test heads-up. The dashboard YAML there shows which data each entity
offers; for an actual dashboard, use the [Somtoday Card](#dashboard-card)
instead — Home Assistant's markdown card strips the styling those examples
would need.

## Known limitations

- **Grades, absences and messages are not exposed.** They are technically
  reachable, but they are the most sensitive data in the account and were left
  out of this version on purpose.
- **Homework completeness depends on the school.** Somtoday spreads study-guide
  items over three endpoints (per lesson, per day, per week); all three are
  fetched, but a school that keeps its homework somewhere else entirely will
  show less than the app does.
- **Cancellation detection is as fast as the poll interval.** A lesson dropped
  five minutes ago shows up at the next refresh, not instantly.
- **No push.** Somtoday has no webhook for this; polling is the only option.
- **The lesson lists in attributes are capped at 30 items** so they stay under
  Home Assistant's state-attribute size limit.

## Alternatives

Somtoday officially supports an **iCalendar token** (Somtoday → Instellingen →
Agenda). If all you want is the timetable in a calendar, that is the supported
route — and Home Assistant's built-in **Remote Calendar** integration reads any
ICS URL, so you do not need this integration for it.

It is not a replacement, though: Somtoday states that the feed carries schedule
appointments only — **no homework and no tests** — and it is not available to
parent accounts. That gap is why this integration talks to the REST API instead.

## Privacy and security

- **Your password never reaches Home Assistant.** You type it on Somtoday's own
  login page.
- **Only the refresh token is stored**, in Home Assistant's own encrypted config
  entry storage. Access tokens live an hour and are re-minted on every restart,
  so none is ever written to disk.
- **Nothing secret is logged.** No token, no login code, no authorization URL is
  written to the log at any level.
- **Diagnostics are structural, not redacted payloads.** The diagnostics file
  contains counts, statuses, timings and field-coverage numbers — no name, class,
  teacher, room, homework topic or token. That is deliberate: diagnostics get
  attached to public issues, and this account belongs to a child.
- **Revoking access**: change the Somtoday password. The stored refresh token
  stops working and Home Assistant will ask you to sign in again.

## Troubleshooting

**Signing in appears to do nothing at all**
Chrome discarded the redirect. Your login worked. See
[If nothing seems to happen after signing in](#if-nothing-seems-to-happen-after-signing-in).

**"That is the address of the Somtoday login page"**
The address bar was copied before the login finished. That URL carries an
`auth=` parameter and no code — the code only exists *after* you have signed
in. **The link in the form is still valid**, so you do not have to start over.

**"No login code found in that address"**
Something without a `code=` in it. The address you need starts with
`somtoday://`, not with `https://`. Copy it whole, from `somtoday://` to the
end. If the browser cleared the address bar after the failed protocol handler,
open the same link again — it is still valid.

**"Somtoday rejected the login code"**
The code expired, or it had already been used. This is the one case where the
form mints a *new* link: use that one, not the tab you still have open.

**"Signed in successfully, but this account has no students linked to it"**
The login worked but Somtoday returned no students. This usually means a staff
or otherwise non-student account.

**Entities show "unavailable"**
Somtoday could not be reached on the last poll. Check the *last update* sensor
for when it last succeeded. Home Assistant retries automatically; a genuinely
dead session raises a re-authentication prompt instead.

**Home Assistant keeps asking me to sign in again**
That happens when Somtoday refuses the stored refresh token. Changing the
Somtoday password does this, and so does signing in on many devices if
Somtoday decides to invalidate older sessions. Somtoday also sometimes refuses
a refresh for a while (HTTP 403 without an error code); version 0.1.x treats
that as a dead session and asks you to sign in straight away. Version 0.2.0
(in beta) retries in the background instead and only asks after six hours of
continuous refusal.

**Something is missing or wrong in the schedule**
Enable debug logging (**Settings → Devices & services → Somtoday → ⋮ → Enable
debug logging**), download diagnostics, and open an issue. The diagnostics file
is safe to attach — it carries no personal data by design.

## Removing the integration

**Settings → Devices & services → Somtoday → ⋮ → Delete**. That removes the
config entry, its devices and its entities, including the stored refresh token.
If you want to be thorough, change your Somtoday password afterwards to
invalidate the token on Somtoday's side too.

## A warning worth repeating

Somtoday publishes no API for this. Everything here is built on
[community documentation](https://github.com/elisaado/somtoday-api-docs) and on
the endpoints Somtoday's own app uses. Two things that documentation got wrong
were already found while building this: the password grant is disabled, and the
school-list endpoint it points at was removed in February 2025. Expect more of
that. If Topicus changes the API, this integration breaks until it is updated —
and it may not be fixable at all if they decide to lock the app client down.

## Development

```
pip install -r requirements.test.txt
pytest
ruff check .
```

The test suite needs a POSIX environment: Home Assistant imports `fcntl` and
`resource` unconditionally, so the harness does not run natively on Windows.
CI runs on Linux.

---

*Independent community project, not affiliated with or endorsed by Somtoday or Topicus. Use at your own risk.*
