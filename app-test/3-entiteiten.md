## Entities

Per student. Entity ids follow your Home Assistant language — the Dutch names
are shown here, an English instance reads `next_lesson`, `next_school_day`,
`open_homework`, `next_test`, `current_lesson`, `last_update`.

| Entity | State | Key attributes |
|---|---|---|
| `calendar.somtoday_<naam>_rooster` | On during a lesson | Standard calendar attributes |
| `sensor.somtoday_<naam>_huidige_les` | Subject, or unknown between lessons | `teacher`, `location`, `start`, `end`, `period_start`, `status`, `cancelled` |
| `sensor.somtoday_<naam>_volgende_les` | Start time (timestamp) | Same as above |
| `sensor.somtoday_<naam>_eerstvolgende_schooldag` | First lesson's start (timestamp) | `date`, `lesson_count`, `cancelled_count`, `last_lesson_end`, `lessons` |
| `sensor.somtoday_<naam>_vandaag` | Today's first lesson (timestamp) | `date`, `lesson_count`, `cancelled_count`, `first_lesson`, `last_lesson_end`, `lessons` |
| `sensor.somtoday_<naam>_openstaand_huiswerk` | Count | `homework` (list of `subject`, `topic`, `description`, `due`, `type`) |
| `sensor.somtoday_<naam>_volgende_toets` | Due moment (timestamp) | `subject`, `topic`, `description`, `type` |
| `sensor.somtoday_<naam>_deze_week` | Lessons in the active week | `week_number`, `deviation_count`, `days` (per day: lessons with `deviates` / `deviation`, plus `missing`) |
| `sensor.somtoday_<naam>_basisrooster` | Recurring lessons in a normal week *(disabled by default)* | `weeks_observed`, `days` |
| `sensor.somtoday_<naam>_roosterwijzigingen` | When the latest timetable change came in (timestamp) | `change_count`, `retention_days`, `changes` (newest first, each with `detected_at`, `type`, `subject`, `start`, `end`, and `previous` where something moved) |
| `sensor.somtoday_<naam>_laatste_update` | Last successful poll (timestamp) | — |

Notes on behaviour that is easy to misread:

- **Cancelled lessons are skipped** by the current-lesson and next-lesson
  sensors — reporting a dropped lesson as "your next lesson" would be worse
  than saying nothing. They stay visible on the calendar with a
  `Vervallen: ` prefix, and in the `lessons` attribute with `cancelled: true`.
- **The next school day rolls over after the last bell**, not at midnight. So
  during the day it describes today, and from the moment the last lesson ends it
  describes the next day with lessons. That is what makes the evening-before
  automation work.
- **Today never rolls forward.** It continues to expose the complete current
  date, including ended and cancelled lessons, until midnight.
- **Undated homework is kept**, sorted last. Somtoday allows study items with no
  due date and hiding them would lose real work.
- **Schedule changes show when they came in, not when the school made them.**
  Somtoday does not say when a lesson was changed — not to this integration
  and not in its own app. `detected_at` is the poll that first saw the change,
  so at most one poll interval after the school made it. The last fourteen days
  are kept in Home Assistant's `.storage` and survive a restart; a change made
  while Home Assistant was not running is not picked up, because the first poll
  after a restart only sets the baseline to compare against.
- **An unrecognised lesson status is reported as `unknown`, never guessed.** If
  you see that, the raw Somtoday value is in the `raw_status` attribute and in
  diagnostics — please [open an issue](https://github.com/jonisnet/ha-somtoday/issues/new)
  so it can be mapped.

## The normal week, and this week against it

Somtoday publishes no base timetable — only a list of concrete appointments. So
the normal week is **derived**: for each weekday and lesson period, whichever
lesson turns up most often across the weeks currently held. Cancelled lessons
are excluded from that vote, since a dropped lesson is precisely the deviation
this exists to measure.

That makes the active-week sensor able to mark *where this week differs*: a
lesson `cancelled`, in a `different_room`, with a `different_teacher`, at a
`different_time`, an `extra` lesson that is not normally there — and, per day, a
`missing` list of normal-week lessons that have no appointment this week at all.
Without that last one a quietly removed lesson would simply be absent, which is
the one change an active-week view could otherwise hide completely.

**How much to trust it depends on how many weeks were fetched.** A slot must
appear in at least two distinct weeks before it counts as normal, and
`weeks_observed` is published so you can see what the derivation had to work
with. That is why the default is eight weeks — at two weeks a recurring
slot is barely distinguishable from a coincidence, from four weeks on it is a
pattern. Lower it only if you do not use the normal-week view. That is also why the base-week sensor is **disabled by
default** — it should be an explicit choice, not something that quietly presents
a one-week guess as the timetable.

## Get notified when the timetable changes

A blueprint does this for you — pick the phones from a list and it works out
the rest. No YAML, no service names to type.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fjonisnet%2Fha-somtoday%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjonisnet%2Fsomtoday_roosterwijziging.yaml)

You only have to answer one question: which phones get the message. Everything
else has a working default. Step-by-step instructions are in
[`examples/README.md`](examples/README.md), and the plain YAML version lives in
[`examples/automations/`](examples/automations/) if you would rather edit the
wording yourself.

Two things that look like a broken setup but are not: nothing arrives until
Somtoday actually changes something, and the first check after a Home Assistant
restart stays quiet on purpose. To see it work straight away, fire the event by
hand — see [Testing a notification](#testing-a-notification-without-waiting-for-a-real-change).

## Schedule-change events

Somtoday has no push, so a change is only visible by comparing one poll against
the last. The integration does that comparison and fires
**`somtoday_schedule_changed`** on the event bus — one event per student per
poll, with every change batched together, so a whole day being dropped is one
notification rather than seven.

```yaml
device_id: <the student's device, so a parent account can route per child>
student_id: 1000001
change_count: 2
cancelled_count: 1
changes:
  - type: cancelled          # or reinstated / moved / room_changed /
    subject: wiskunde        # teacher_changed / added / removed
    teacher: abc
    location: "204"
    start: "2026-09-02T13:45:00+02:00"
    end: "2026-09-02T14:30:00+02:00"
    period_start: 7
    cancelled: true
    previous:                # the same fields, as they were before
      teacher: abc
      location: "204"
      start: "2026-09-02T13:45:00+02:00"
      end: "2026-09-02T14:30:00+02:00"
      period_start: 7
      cancelled: false
```

Three rules keep this from crying wolf:

- **Nothing fires on the first poll after a restart.** The previous timetable is
  held in memory only, so every restart would otherwise look like the whole
  schedule had just appeared.
- **Lessons that have already started are ignored.** They cannot usefully
  change, and the fetch window drops old days as it slides forward.
- **The newly revealed far edge is ignored.** The window moves forward about a
  day per poll; without this, every poll would announce a fresh day's worth of
  "added" lessons.

`previous` is absent for `added` (there was no earlier version) and for
`removed` (there is no later one — the change's own fields *are* the old
lesson).

### Testing a notification without waiting for a real change

The Run button on an automation does **not** work for this: it executes the
actions with no trigger, so `trigger.event.data` does not exist and the
template fails with `UndefinedError: 'trigger' is undefined`. That says nothing
about whether the automation works.

Fire the event instead — **Developer tools → Events**, event type
`somtoday_schedule_changed`, and this as the event data:

```yaml
device_id: null
student_id: 1000001
change_count: 2
cancelled_count: 1
changes:
  - type: cancelled
    subject: wiskunde
    teacher: abc
    location: "204"
    start: "2026-09-02T13:45:00+02:00"
    end: "2026-09-02T14:30:00+02:00"
    period_start: 7
    cancelled: true
    previous:
      teacher: abc
      location: "204"
      start: "2026-09-02T13:45:00+02:00"
      end: "2026-09-02T14:30:00+02:00"
      period_start: 7
      cancelled: false
  - type: room_changed
    subject: Engelse taal
    teacher: def
    location: "118"
    start: "2026-09-02T11:45:00+02:00"
    end: "2026-09-02T12:30:00+02:00"
    period_start: 5
    cancelled: false
    previous:
      teacher: def
      location: "203"
      start: "2026-09-02T11:45:00+02:00"
      end: "2026-09-02T12:30:00+02:00"
      period_start: 5
      cancelled: false
```

That drives the real automation end to end, notify services included.

**If the notification never arrives**, check the service names first. Under
**Developer tools → Actions**, type `notify.` and copy what is actually listed —
mobile devices are usually `notify.mobile_app_<device>`. Use the full name,
including the `notify.` prefix: the example passes it through unchanged.

