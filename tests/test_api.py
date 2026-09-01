"""Tests for the Somtoday REST client."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.somtoday.api import SomtodayApiClient, SomtodayApiError
from custom_components.somtoday.auth import (
    SomtodayAuth,
    SomtodayInvalidAuth,
    SomtodayTokens,
)
from custom_components.somtoday.const import TOKEN_URL

from . import payloads

API = "https://api.example.invalid"
STUDENTS_URL = re.compile(rf"^{re.escape(API)}/rest/v1/leerlingen.*$")
APPOINTMENTS_URL = re.compile(rf"^{re.escape(API)}/rest/v1/afspraken.*$")
ACCOUNT_URL = re.compile(rf"^{re.escape(API)}/rest/v1/account/me.*$")
HOMEWORK_APPOINTMENT_URL = re.compile(
    rf"^{re.escape(API)}/rest/v1/studiewijzeritemafspraaktoekenningen.*$"
)
HOMEWORK_DAY_URL = re.compile(
    rf"^{re.escape(API)}/rest/v1/studiewijzeritemdagtoekenningen.*$"
)
HOMEWORK_WEEK_URL = re.compile(
    rf"^{re.escape(API)}/rest/v1/studiewijzeritemweektoekenningen.*$"
)


@pytest.fixture
async def session():
    """Return a throwaway aiohttp session."""
    async with aiohttp.ClientSession() as client_session:
        yield client_session


def _client(session, *, expires_in_seconds: int = 3600) -> SomtodayApiClient:
    tokens = SomtodayTokens(
        access_token="cached-token",
        refresh_token="fictional-refresh-token",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds),
        api_url=API,
    )
    return SomtodayApiClient(session, SomtodayAuth(session, tokens))


async def test_get_students(session):
    client = _client(session)
    with aioresponses() as mocked:
        mocked.get(STUDENTS_URL, payload=payloads.STUDENTS, status=200)
        students = await client.async_get_students()

    assert len(students) == 1
    assert students[0]["roepnaam"] == "Fien"


async def test_get_appointments_sends_the_date_window(session):
    client = _client(session)
    with aioresponses() as mocked:
        mocked.get(
            APPOINTMENTS_URL, payload={"items": [payloads.appointment()]}, status=200
        )
        await client.async_get_appointments(date(2026, 9, 7), date(2026, 9, 21))
        request_url = str(next(iter(mocked.requests.values()))[0].kwargs["params"])

    assert "begindatum" in request_url
    assert "2026-09-07" in request_url
    assert "2026-09-21" in request_url


async def test_missing_items_key_yields_an_empty_list(session):
    """Somtoday omits ``items`` when there is nothing to return."""
    client = _client(session)
    with aioresponses() as mocked:
        mocked.get(APPOINTMENTS_URL, payload={}, status=200)
        assert await client.async_get_appointments(date(2026, 9, 7), date(2026, 9, 8)) == []


async def test_206_is_a_success_not_a_failure(session):
    """Somtoday answers paginated lists with 206; treating it as an error
    made the integration look completely unable to read anything."""
    client = _client(session)
    with aioresponses() as mocked:
        mocked.get(STUDENTS_URL, payload=payloads.STUDENTS, status=206)
        students = await client.async_get_students()

    assert len(students) == 1


async def test_pagination_walks_every_page(session):
    """A fortnight of lessons runs past one page; stopping early loses them."""
    client = _client(session)
    page_one = [payloads.appointment(900000 + i) for i in range(100)]
    page_two = [payloads.appointment(900100 + i) for i in range(50)]

    with aioresponses() as mocked:
        mocked.get(
            APPOINTMENTS_URL,
            payload={"items": page_one},
            status=206,
            headers={"Content-Range": "items 0-99/150"},
        )
        mocked.get(
            APPOINTMENTS_URL,
            payload={"items": page_two},
            status=206,
            headers={"Content-Range": "items 100-149/150"},
        )
        items = await client.async_get_appointments(date(2026, 9, 7), date(2026, 9, 21))

    assert len(items) == 150


async def test_pagination_asks_for_the_next_range(session):
    """The second request must ask for the range after the first, not repeat it."""
    client = _client(session)
    full_page = [payloads.student(1000000 + i) for i in range(100)]

    with aioresponses() as mocked:
        mocked.get(
            STUDENTS_URL,
            payload={"items": full_page},
            status=206,
            headers={"Content-Range": "items 0-99/150"},
        )
        mocked.get(
            STUDENTS_URL,
            payload={"items": full_page[:50]},
            status=206,
            headers={"Content-Range": "items 100-149/150"},
        )
        await client.async_get_students()
        sent = [
            call.kwargs["headers"].get("Range")
            for calls in mocked.requests.values()
            for call in calls
        ]

    assert sent == ["items=0-99", "items=100-199"]


async def test_206_without_a_content_range_stops_after_one_page(session):
    """A short page is the last page, Content-Range or not."""
    client = _client(session)
    with aioresponses() as mocked:
        mocked.get(STUDENTS_URL, payload=payloads.STUDENTS, status=206)
        assert len(await client.async_get_students()) == 1


async def test_an_unknown_total_stops_after_one_page(session):
    client = _client(session)
    with aioresponses() as mocked:
        mocked.get(
            STUDENTS_URL,
            payload=payloads.STUDENTS,
            status=206,
            headers={"Content-Range": "items 0-99/*"},
        )
        assert len(await client.async_get_students()) == 1


async def test_a_range_that_does_not_advance_cannot_loop_forever(session):
    """A server repeating the same range would otherwise spin until timeout.

    ``repeat=True`` means aioresponses would answer for ever, so this test
    hanging or exhausting _MAX_PAGES is the failure it guards against.
    """
    client = _client(session)
    with aioresponses() as mocked:
        mocked.get(
            STUDENTS_URL,
            payload=payloads.STUDENTS,
            status=206,
            headers={"Content-Range": "items 0-0/999"},
            repeat=True,
        )
        students = await client.async_get_students()

    assert len(students) == 1


async def test_expired_token_is_refreshed_before_the_call(session):
    client = _client(session, expires_in_seconds=-10)
    with aioresponses() as mocked:
        mocked.post(TOKEN_URL, payload=payloads.ROTATED_TOKEN_RESPONSE, status=200)
        mocked.get(STUDENTS_URL, payload=payloads.STUDENTS, status=200)
        await client.async_get_students()

    assert client.auth.tokens.access_token == "fictional-access-token-2"


async def test_401_triggers_one_refresh_and_a_retry(session):
    """A token rejected mid-flight is refreshed rather than surfaced."""
    client = _client(session)
    with aioresponses() as mocked:
        mocked.get(STUDENTS_URL, status=401)
        mocked.post(TOKEN_URL, payload=payloads.ROTATED_TOKEN_RESPONSE, status=200)
        mocked.get(STUDENTS_URL, payload=payloads.STUDENTS, status=200)
        students = await client.async_get_students()

    assert len(students) == 1


async def test_401_after_a_refresh_is_a_definitive_failure(session):
    client = _client(session)
    with aioresponses() as mocked:
        mocked.get(STUDENTS_URL, status=401)
        mocked.post(TOKEN_URL, payload=payloads.ROTATED_TOKEN_RESPONSE, status=200)
        mocked.get(STUDENTS_URL, status=401)
        with pytest.raises(SomtodayInvalidAuth):
            await client.async_get_students()


async def test_403_is_retryable_not_a_reauth(session):
    """Permission denied is not a dead session."""
    client = _client(session)
    with aioresponses() as mocked:
        mocked.get(STUDENTS_URL, status=403)
        with pytest.raises(SomtodayApiError) as excinfo:
            await client.async_get_students()

    assert not isinstance(excinfo.value, SomtodayInvalidAuth)
    assert excinfo.value.status == 403


async def test_server_error_is_retryable(session):
    client = _client(session)
    with aioresponses() as mocked:
        mocked.get(STUDENTS_URL, status=502)
        with pytest.raises(SomtodayApiError) as excinfo:
            await client.async_get_students()

    assert excinfo.value.status == 502


async def test_unexpected_body_shape_is_rejected(session):
    client = _client(session)
    with aioresponses() as mocked:
        mocked.get(STUDENTS_URL, payload=["not", "a", "dict"], status=200)
        with pytest.raises(SomtodayApiError):
            await client.async_get_students()


async def test_homework_merges_all_three_endpoints(session):
    """Skipping a variant would silently lose homework."""
    client = _client(session)
    with aioresponses() as mocked:
        mocked.get(
            HOMEWORK_APPOINTMENT_URL,
            payload={"items": [payloads.homework(800001)]},
            status=200,
        )
        mocked.get(
            HOMEWORK_DAY_URL, payload={"items": [payloads.homework(800002)]}, status=200
        )
        mocked.get(
            HOMEWORK_WEEK_URL,
            payload={"items": [payloads.homework(800003)]},
            status=200,
        )
        items = await client.async_get_homework(date(2026, 9, 7), payloads.STUDENT_A_ID)

    assert len(items) == 3


async def test_one_failing_homework_endpoint_does_not_sink_the_others(session):
    """Partial homework beats none — the endpoints fail independently."""
    client = _client(session)
    with aioresponses() as mocked:
        mocked.get(HOMEWORK_APPOINTMENT_URL, status=500)
        mocked.get(
            HOMEWORK_DAY_URL, payload={"items": [payloads.homework(800002)]}, status=200
        )
        mocked.get(
            HOMEWORK_WEEK_URL,
            payload={"items": [payloads.homework(800003)]},
            status=200,
        )
        items = await client.async_get_homework(date(2026, 9, 7))

    assert len(items) == 2


async def test_dead_session_during_homework_still_propagates(session):
    """A retryable per-endpoint failure is tolerated; a dead session is not."""
    client = _client(session)
    with aioresponses() as mocked:
        mocked.get(HOMEWORK_APPOINTMENT_URL, status=401)
        mocked.post(TOKEN_URL, payload=payloads.INVALID_GRANT_RESPONSE, status=400)
        with pytest.raises(SomtodayInvalidAuth):
            await client.async_get_homework(date(2026, 9, 7))


async def test_account_lookup_degrades_to_an_empty_dict(session):
    """The account endpoint is the least documented one; it must not block setup."""
    client = _client(session)
    with aioresponses() as mocked:
        mocked.get(ACCOUNT_URL, status=404)
        assert await client.async_get_account() == {}


async def test_account_lookup_success(session):
    client = _client(session)
    with aioresponses() as mocked:
        mocked.get(ACCOUNT_URL, payload=payloads.ACCOUNT, status=200)
        account = await client.async_get_account()

    assert account["links"][0]["id"] == 555000
