# Somtoday

[![Version](https://img.shields.io/github/v/release/jonisnet/ha-somtoday?style=flat-square&label=version&color=blue&cacheSeconds=86400)](https://github.com/jonisnet/ha-somtoday/releases/latest)
[![Marketplace](https://img.shields.io/badge/Marketplace-custom_repository-orange?style=flat-square)](https://www.home-assistant.io/integrations/marketplace/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](https://github.com/jonisnet/ha-somtoday/blob/main/LICENSE)
[![HA](https://img.shields.io/badge/Home%20Assistant-2024.11%2B-41bdf5?style=flat-square)](https://www.home-assistant.io)
[![Downloads](https://img.shields.io/github/downloads/jonisnet/ha-somtoday/total?style=flat-square&label=downloads&cacheSeconds=86400)](https://github.com/jonisnet/ha-somtoday/releases)

> **⚠️ Unofficial API.** Somtoday never published this API, and does not
> support it being used this way. The integration works today, but Topicus can
> change or block it without warning — a version that works this morning can
> break this afternoon with nothing changed on your side. Versions stay in the
> `0.x.x` range while that risk is live; see [CHANGELOG.md](CHANGELOG.md).

Brings a student's Somtoday timetable, homework and tests into Home Assistant.
Somtoday itself stays read-only. Optional personal appointments are stored only
in Home Assistant's local `.storage`; nothing is written back to Somtoday.

## What you get

- **A calendar** with the full timetable, including dropped and moved lessons.
- **The current lesson** and **the next lesson**, with subject, room, teacher
  and lesson period.
- **The next school day** — first lesson, last bell, and the whole day's lesson
  list as an attribute. This is what makes an "evening before" notification
  possible.
- **Today** — the complete current school day, which stays on today after the
  last bell instead of rolling forward.
- **Open homework** and **the next test**.
- A local **planner** sensor per student for appointments around the school day.
  Administrators can edit it; additional users can be selected in the
  integration options. Everyone else has read-only access.
- A **last update** diagnostic sensor, so a silently stalled integration is
  visible instead of just looking like a quiet week.

One Home Assistant device per student, so a parent account with two children
gets two clean sets of entities.

## Dashboard card

The integration has its own Lovelace card:
**[Somtoday Card](https://github.com/jonisnet/ha-somtoday-card)** — a week
timetable, today and tomorrow, homework and tests, with lessons, homework and
tests opening in a dialog. Add it to a dashboard and it fills itself in for
every child on the account; there is nothing to type.

With more than one child it gets a row of buttons per child, and can show every
child's day side by side.

![Somtoday Card on a wide screen](https://raw.githubusercontent.com/jonisnet/ha-somtoday-card/main/preview/wide-dark.png)

Install it from the Marketplace with **Add from link** (or, before Home Assistant
2026.11, from HACS as a **Dashboard** custom repository):
`https://github.com/jonisnet/ha-somtoday-card`.

[![Open in the Marketplace](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jonisnet&repository=ha-somtoday-card&category=plugin)

All names and lessons in the screenshot are fictional.

## Requirements

- Home Assistant **2024.11.0** or newer.
- A Somtoday student or parent login. **The iCalendar token is not enough** —
  see [Alternatives](#alternatives) below.
- A browser on the machine you set this up from. The login happens there, not
  in Home Assistant.

## Installation

### Via the Marketplace (recommended)

Home Assistant 2026.11 and newer have HACS built in as the **Marketplace**.

[![Open in the Marketplace](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jonisnet&repository=ha-somtoday&category=integration)

1. Click the button above, or go to **Settings → Marketplace → Add from link** and enter `https://github.com/jonisnet/ha-somtoday` (this needs the Marketplace's GitHub connection)
2. Install **Somtoday**
3. Restart Home Assistant when the Marketplace asks for it (a repair offers the restart)

### Via HACS (Home Assistant before 2026.11)

1. Go to **HACS → Integrations → ⋮ → Custom repositories**
2. Add `https://github.com/jonisnet/ha-somtoday` with category **Integration**
3. Search for **Somtoday**, install it and restart Home Assistant

On updating to Home Assistant 2026.11, an installation from HACS moves to the Marketplace by itself.

### Manual

1. Copy `custom_components/somtoday` into your Home Assistant config's
   `custom_components` directory.
2. Restart Home Assistant.

