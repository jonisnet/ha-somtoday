# Examples

Copy-paste starting points. **Replace every `<naam>` with your own student's
slug** — the integration names entities after the device, so a student called
Fien gets `sensor.somtoday_fien_voorbeeld_volgende_les`. Look the exact ids up
under **Settings → Devices & services → Somtoday → the student's device**.

Entity ids follow your Home Assistant language. On a Dutch instance they read
`volgende_les`, `eerstvolgende_schooldag`, `openstaand_huiswerk`,
`volgende_toets`; on an English one they read `next_lesson`,
`next_school_day`, `open_homework`, `next_test`.

`notify.mobile_app_<device>` also needs replacing with your own notify service.

## Blueprint

The notification is also available as a blueprint, which is the easier route:
it asks which phones to notify and where each of them should land, and works
out the notify service names from the devices you pick.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fjonisnet%2Fha-somtoday%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjonisnet%2Fsomtoday_roosterwijziging.yaml)

The YAML automation below does the same thing and is the place to look if you
want to change the wording or the filtering by hand.

## Automations

| File | What it does |
|---|---|
| `notify_tomorrows_first_lesson.yaml` | Every evening at 20:00, a summary of the next school day |
| `notify_before_first_lesson.yaml` | A reminder 45 minutes before the first lesson starts |
| `notify_schedule_changed.yaml` | **Any** timetable change, to several devices, with was/now side by side |
| `notify_cancelled_lesson.yaml` | Cancellations on the next school day only — `notify_schedule_changed` supersedes this |
| `notify_upcoming_test.yaml` | A heads-up two days before a test |

## Dashboards

| File | What it shows |
|---|---|
| `schedule_calendar.yaml` | The timetable as a calendar card |
| `somtoday_overzicht.yaml` | Full overview: status, day timetable, next test, homework |
| `weekrooster_horizontaal.yaml` | **The week as coloured lesson blocks in columns side by side**, wrapping on narrow screens. One card for a sections view |
| `huiswerk_en_toets.yaml` | **Homework and the next test as coloured blocks**, each expandable to the teacher's full description |
| `weekrooster.yaml` | Table-style variant of the week view, plus the derived normal week |
| `today_overview.yaml` | A simpler variant of the same idea |
