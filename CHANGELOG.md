# Changelog

All notable changes to the Somtoday integration are documented here.
Development happens in beta releases (`X.Y.Z-beta.N`) between occasional real
releases, where this file is consolidated into one summary per real version.

## Unreleased

### Changed
- **The example notification only covers the next few days now**, and links
  each recipient to their own dashboard. Two days ahead normally; Thursday and
  Friday reach through to Monday, because a Friday change to Monday's timetable
  is exactly what is worth knowing on Friday. A change three weeks out no longer
  interrupts anyone, and when every change falls outside the window nothing is
  sent at all.

  This filtering deliberately lives in the automation rather than in the
  integration: the event reports every change it can see, and how far ahead is
  worth interrupting someone for is a question about people, not about data.
  Adjusting it needs no new release.

  Only `examples/` changed, so the installed component is byte-identical to
  0.1.2 and there is no release for this.

## 0.1.2

### Fixed
- **A newly published week is no longer announced as eight new lessons.**
  Schools release the timetable a fortnight or so ahead, and a day nobody had
  any data for suddenly having lessons was being reported as eight `added`
  changes. That is the schedule being published, not changed. Only lessons
  landing on a day that already had lessons count as an insertion now — a
  genuinely extra lesson is still reported.
- **The example automation logged a template error per change per device.**
  It read `c.previous`, which does not exist on an `added` change; a missing
  key is an error in Home Assistant's templating even when the surrounding
  `if` handles it. It uses `c.get('previous')` now. The message itself was
  always correct — this only filled the log.

## 0.1.1

### Fixed
- **The calendar no longer loses days you have already lived through.** The
  fetch window looked back a fixed one day, so at midnight the day before
  yesterday simply vanished. It now reaches back to the Monday of the current
  week, so the week you are in keeps all of its days and rolls over only when
  the next week starts. A timetable is a record of the week as much as a plan
  for it, and quietly rewriting the part that already happened is worse than
  fetching a few extra days.

  This does not change notifications: change detection has always ignored
  lessons that have already started, so days dropping off the back of the
  window could never produce a "removed" message. There are now tests naming
  that scenario explicitly.

## 0.1.0

### Fixed
- **Removed a real first name from the example automation.** The
  notification example listed notify services named after an actual child
  (`mobile_app_telefoon_van_<name>`); they are now generic. No school name,
  surname or account identifier was ever present in this repository.

## 0.0.2

### Changed
- **The schedule window now defaults to four weeks instead of two.** The normal
  week is derived from the pattern across the weeks actually held, so the window
  doubles as the sample size for that derivation: at two weeks a recurring slot
  is barely distinguishable from a coincidence, at four it is a pattern. The
  30-day choice became 28 so the options read 1, 2, 3 and 4 weeks.
- An entry configured with the old 30-day window now falls back to the default
  in the options form rather than opening a blank dropdown, which would read as
  the setting having been lost.

### Fixed
- **Four sensors had no name.** `today`, `next_week`, `upcoming_work` and
  `planner` referenced translation keys that were never declared, so Home
  Assistant had nothing to call them. Three were also missing an icon. hassfest
  does not check that a `translation_key` used in code actually exists, which is
  why CI passed on 0.0.1 with this in it.

### Added
- A release workflow that builds `somtoday.zip` from the released tag and
  attaches it, and fails the release if `manifest.json` and the tag disagree.

## 0.0.1

First release. Read-only access to a student's Somtoday timetable, homework and
tests.

### Added
- **Next-week and upcoming-schoolwork sensors** for richer dashboard timelines.
- **Local student planner** stored in Home Assistant, with add/delete services,
  administrator access and an option to grant specific users edit rights.
- **Today sensor** with the complete current school day. Unlike the next-school-
  day sensor, it keeps today's finished and cancelled lessons visible until
  midnight.
- **Authorization code + PKCE login**, completed in the user's own browser and
  finished by pasting the failed `somtoday://` redirect back into Home
  Assistant. Works for every school, including SSO and two-factor ones, and no
  password ever reaches Home Assistant. Only the refresh token is persisted;
  rotated tokens are written back on every poll.
- **Re-authentication flow** using the same paste step, guarded so that signing
  in with a *different* Somtoday account aborts instead of silently rebinding
  the existing entities.
- **One device per student**, so a parent account with several children gets a
  separate, unambiguous set of entities for each.
- **`calendar` entity** with the full timetable. Cancelled lessons stay visible
  with a `Vervallen: ` prefix rather than disappearing, so a dropped lesson is
  distinguishable from an empty slot. Any period outside the cached window is
  fetched on demand instead of coming back empty.
- **Sensors**: current lesson, next lesson, next school day (with the whole
  day's lesson list as an attribute), open homework, next test, and a
  diagnostic last-update sensor.
- **Options flow**: refresh interval (15 minutes to 4 hours, default 30) and how
  far ahead the schedule is pre-fetched (1 to 4 weeks, default 2).
- **Dutch and English translations**, with Dutch error messages that explain
  what to do rather than what failed.
- **Diagnostics** reporting structure rather than redacted payloads — counts,
  statuses, timings and field coverage, with no name, teacher, room, homework
  topic or token in the output. The account belongs to a child and diagnostics
  end up on public issues.
- **Example automations and dashboard cards** in `examples/`.

### Fixed
- **Copying the address bar before the login finished now says so.** That URL
  carries an `auth=` parameter and no code, and the generic "no login code
  found" gave the user nothing to act on. It is the most common mistake by far,
  so it gets its own message telling them to finish signing in first.
- **A wrong paste no longer invalidates the login the user has open.** The
  authorization link used to be regenerated on every re-render, including after
  a paste that never reached Somtoday. The code they were about to receive was
  bound to the *old* challenge, so a new verifier turned a recoverable typo into
  a PKCE mismatch and a second full login. The link is now only replaced when it
  is genuinely spent — after a rejected code, a failed exchange, or an account
  with no students.

- **Chrome users could not complete the login at all.** Chrome silently
  discards redirects to schemes it has no handler for - no error page, no
  address-bar change - so the `somtoday://` redirect carrying the code was
  invisible, and the instructions ("copy the address from the address bar")
  described something that never appears there. Somtoday's OAuth client rejects
  every alternative redirect address with HTTP 400, so there is no redirect a
  browser could show instead. The config flow now says this outright and walks
  through reading the `location` header out of DevTools, and the input field
  accepts that paste: it looks for the code anywhere in the text, so a whole
  `location:` line or a block of copied response headers works.

- **The DevTools instructions pointed at the wrong request.** They said to
  click the topmost entry in Chrome's Network list, but Chrome appends new
  requests to the *bottom* - so the top entry is the original authorization
  request, whose `location` points straight back at the login page. Following
  the instructions literally produced exactly the symptom they were meant to
  cure. They now say to search the request list for `somtoday://` instead,
  which lands on the right response regardless of ordering.

- **"Could not reach Somtoday" now says why, in the log.** The token exchange
  is the first request Home Assistant itself makes to Somtoday - everything
  before it happens in the user's browser - so it is exactly where a DNS,
  firewall or outage problem on the Home Assistant host first surfaces. Both
  that failure and a failing account read are logged at error level with the
  underlying status, instead of at debug: asking someone to enable debug
  logging for an integration they have not managed to add yet means editing
  `configuration.yaml`. The two are worded differently, because "cannot reach
  the login host" and "signed in but cannot reach the data API" have different
  fixes. Neither message can contain a code or a token.

- **Somtoday's `206 Partial Content` was treated as a failure, and its list
  endpoints are paginated.** Reading the student list came back 206, the client
  accepted only 200, and setup died with "Could not reach Somtoday" on an
  account that had signed in perfectly. Beyond unblocking setup this was a
  correctness bug waiting to happen: only the first page was ever read, and a
  fortnight of lessons passes a hundred appointments easily, so the schedule
  would have been silently short. The client now walks the pages, requesting
  `Range: items=…` and stopping on a short page - a rule that holds whether or
  not the server sends a usable `Content-Range`, and that cannot be talked into
  looping by a header that never advances.

### Added
- **Schedule-change detection**, fired as `somtoday_schedule_changed`. The
  coordinator compares each poll against the previous one and reports lessons
  cancelled, reinstated, moved, given another room or teacher, added or
  removed — each with a `previous` block holding the same fields as they were,
  so a notification can put "was" and "now" side by side without special-casing
  per change type. One event per student per poll with every change batched, so
  a whole day being dropped is one notification rather than seven, and a
  `device_id` on the payload so a parent account can route per child.
- **`examples/automations/notify_schedule_changed.yaml`** — notifies several
  devices, showing the old and new details together.
- **`examples/dashboards/somtoday_overzicht.yaml`** — status, the day's
  timetable with the current lesson marked and cancellations struck through,
  the next test with a countdown, and homework by due date. Built from Home
  Assistant's own cards, so it needs no extra install.

- **A derived normal week, and the active week measured against it.** Somtoday
  has no base-timetable endpoint, so the normal week is worked out from the
  pattern: per weekday and lesson period, the most common lesson across the
  weeks held, requiring at least two distinct weeks before a slot counts and
  excluding cancelled lessons from the vote. The active-week sensor then marks
  each lesson as `cancelled`, `different_room`, `different_teacher`,
  `different_time`, `extra` or unchanged, and lists per day the normal-week
  lessons that have no appointment this week — otherwise a quietly removed
  lesson would just be absent. `weeks_observed` is published so the derivation
  can be read with the right amount of trust, and the base-week sensor is
  disabled by default rather than quietly presenting a one-week guess as the
  timetable.
- **`examples/dashboards/weekrooster.yaml`** — the active week with deviations
  flagged, and the normal week underneath.

### Fixed
- **The notification example built the service name with a trap in it.** It
  prefixed `notify.` onto each `for_each` entry, so pasting the full service
  name — which is what Developer tools → Actions shows, and therefore what
  anyone would paste — produced `notify.notify.<device>` and a "Template
  rendered invalid service" error. The list now takes full service names and
  the action passes them through unchanged.
- **A manual Run no longer looks like a broken template.** Pressing Run
  executes the actions with no trigger, so `trigger.event.data` raised
  `UndefinedError` — which reads as a broken automation rather than as "this
  needs an event". Both templates now fall back to a short explanation, and the
  README documents firing the event from Developer tools to test it properly.

- **`subject_short` on the week views**, because a horizontal week grid needs
  abbreviations — "lichamelijke opvoeding" wraps into an unreadable stack in a
  150px column.
- **`examples/dashboards/weekrooster_horizontaal.yaml`** — the week as coloured
  lesson blocks in columns side by side, laid out with `flex-wrap` rather than
  a fixed column count, so it reflows on the width actually available instead
  of on guessed breakpoints. A single card, for a sections view.
- **`examples/dashboards/huiswerk_en_toets.yaml`** — homework and the next test
  as colour-accented blocks, each with the teacher's full description behind a
  native `<details>` expander. A markdown card has no `tap_action` and no click
  handler, so a real dialog needs a custom card or browser_mod; a disclosure
  element is the interaction that is actually available without a dependency.

### Deliberate decisions
- **The `somtodaypython` package is not used.** It is synchronous (its async
  support was explicitly removed), had no release after August 2025, warns in
  its own README to expect authentication bugs, and does not implement SSO.
  Home Assistant needs async I/O, so the client lives in `api.py` instead.
- **No username/password login.** Somtoday disabled the OAuth2 password grant
  (`Password grant is disabled for insecure clients`), and the alternative —
  POSTing to the login page's form fields — is HTML scraping that breaks on
  every UI change and fails outright at SSO schools.
- **No school picker.** The community docs look schools up in
  `servers.somtoday.nl/organisaties.json`, which Somtoday removed in February
  2025. Omitting `tenant_uuid` from the authorization request makes Somtoday
  show its own school picker instead, which is both more robust and less work
  for the user.
- **No iCalendar mode.** Somtoday's official iCalendar feed carries schedule
  appointments only — no homework, no tests — and is unavailable to parent
  accounts. Home Assistant's built-in Remote Calendar integration already reads
  any ICS URL, so reimplementing it here would duplicate core for less data.
- **Grades, absences and messages are out of scope for now.** They are
  reachable, but they are the most sensitive data in the account.
- **An unrecognised lesson status is reported as `unknown`, never guessed.**
  Silently reporting a dropped lesson as scheduled would be worse than an
  honest gap, so unmapped values are surfaced with a one-shot warning and a
  link to open an issue.
