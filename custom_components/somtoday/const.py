"""Constants for the Somtoday integration."""
from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "somtoday"

PLATFORMS = [Platform.CALENDAR, Platform.SENSOR]

ATTRIBUTION = "Data provided by Somtoday"

# Fired on the event bus when a poll finds the timetable has changed since
# the previous one. One event per student per poll, carrying every change
# together — a whole day being dropped is one piece of news, not seven.
EVENT_SCHEDULE_CHANGED = "somtoday_schedule_changed"

# --------------------------------------------------------------------------
# OAuth2 / PKCE
# --------------------------------------------------------------------------
# Somtoday's own mobile app ("Somtoday Leerling") is a public OAuth2 client
# using authorization-code + PKCE against a custom URI scheme. We reuse that
# public client id: it is the only flow that still works for every school,
# including the ones behind Microsoft/Google SSO or MFA, because the user
# authenticates on Somtoday's real login page in their own browser.
#
# The password grant documented by the community is dead — Somtoday answers
# "Invalid grant: Password grant is disabled for insecure clients" — and the
# alternative of POSTing to the login page's Wicket form fields is HTML
# scraping that breaks on every UI change. Do not reintroduce either.
ORGANISATIONS_URL = "https://servers.somtoday.nl/organisaties.json"
AUTHORIZE_URL = "https://inloggen.somtoday.nl/oauth2/authorize"
TOKEN_URL = "https://inloggen.somtoday.nl/oauth2/token"
CLIENT_ID = "somtoday-leerling-native"
REDIRECT_URI = "somtoday://nl.topicus.somtoday.leerling/oauth/callback"
OAUTH_SCOPE = "openid"

# Fallback for the base URL; the token response carries the real one in
# ``somtoday_api_url`` and that value always wins.
DEFAULT_API_URL = "https://api.somtoday.nl"

# Refresh the access token this many seconds before it actually expires, so a
# request never races the expiry.
TOKEN_EXPIRY_MARGIN = 60

# --------------------------------------------------------------------------
# Config entry keys
# --------------------------------------------------------------------------
CONF_REFRESH_TOKEN = "refresh_token"
CONF_API_URL = "api_url"
CONF_TENANT = "tenant"
CONF_ACCOUNT_ID = "account_id"

# Config-flow form fields.
CONF_AUTH_URL = "auth_url"
CONF_REDIRECT_URL = "redirect_url"

# --------------------------------------------------------------------------
# Options
# --------------------------------------------------------------------------
# How often to poll Somtoday. A school schedule changes rarely, so the default
# is deliberately gentle; 15 minutes is the floor for the same reason.
CONF_REFRESH_INTERVAL = "refresh_interval"
REFRESH_INTERVAL_OPTIONS = (15, 30, 60, 120, 240)
DEFAULT_REFRESH_INTERVAL = 30

# How far ahead the coordinator pre-fetches the schedule. The calendar entity
# can still serve any range — anything outside this window triggers a direct
# fetch — but this is what the sensors and the cached calendar view cover.
CONF_DAYS_AHEAD = "days_ahead"
CONF_PLANNER_ALLOWED_USERS = "planner_allowed_users"
DAYS_AHEAD_OPTIONS = (7, 14, 21, 28)
# Four weeks by default. The normal week is derived from the pattern across the
# weeks actually held (see weeks.py), so the window doubles as the sample size:
# at two weeks a recurring slot is barely distinguishable from a coincidence,
# at four it is a pattern. Fetching more costs one extra page or two per poll,
# which is cheap next to publishing a base week nobody should trust.
DEFAULT_DAYS_AHEAD = 28

SERVICE_PLANNER_ADD = "planner_add"
SERVICE_PLANNER_DELETE = "planner_delete"

# Yesterday is kept in the window so a calendar view of "this week" always has
# the past days of that week available without an extra fetch.
DAYS_BEHIND = 1

# Cap the number of items published as state attributes so the lists stay well
# under Home Assistant's ~16 KB state-attribute limit.
MAX_ATTRIBUTE_ITEMS = 30


class LessonStatus(StrEnum):
    """Canonical status of a scheduled lesson.

    Derived from Somtoday's ``afspraakStatus`` field, which is a free-form
    string rather than a documented enum — hence the explicit mapping plus an
    ``UNKNOWN`` catch-all instead of trusting the raw value.
    """

    SCHEDULED = "scheduled"    # Normal, as-planned lesson
    CANCELLED = "cancelled"    # Dropped; the class does not take place
    MOVED = "moved"            # Rescheduled to another time, room or teacher
    UNKNOWN = "unknown"        # Raw status we have not mapped yet


class HomeworkType(StrEnum):
    """Canonical type of a study-guide item."""

    HOMEWORK = "homework"      # Somtoday ``HUISWERK``
    TEST = "test"              # Somtoday ``TOETS``
    LARGE_TEST = "large_test"  # Somtoday ``GROTE_TOETS``
    UNKNOWN = "unknown"


# Somtoday ``huiswerkType`` → canonical HomeworkType.
HOMEWORK_TYPE_MAP = {
    "HUISWERK": HomeworkType.HOMEWORK,
    "TOETS": HomeworkType.TEST,
    "GROTE_TOETS": HomeworkType.LARGE_TEST,
}

# Both test flavours count as "a test" for the next-test sensor.
TEST_TYPES = frozenset({HomeworkType.TEST, HomeworkType.LARGE_TEST})

# Somtoday ``afspraakStatus`` → canonical LessonStatus. The vocabulary is not
# documented anywhere; these are the values observed in the wild. Anything
# else maps to UNKNOWN and is logged once, with a link to open an issue.
LESSON_STATUS_MAP = {
    "ACTIEF": LessonStatus.SCHEDULED,
    "NORMAAL": LessonStatus.SCHEDULED,
    "GEANNULEERD": LessonStatus.CANCELLED,
    "VERVALLEN": LessonStatus.CANCELLED,
    "UITVAL": LessonStatus.CANCELLED,
    "VERPLAATST": LessonStatus.MOVED,
    "GEWIJZIGD": LessonStatus.MOVED,
}

NEW_ISSUE_URL = "https://github.com/jonisnet/ha-somtoday/issues/new"
