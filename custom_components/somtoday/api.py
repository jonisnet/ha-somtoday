"""Somtoday REST API client.

A deliberately small async client over the endpoints this integration actually
needs. The ``somtodaypython`` package on PyPI was evaluated and rejected: it is
synchronous (its async support was explicitly removed), had no release after
August 2025, warns in its own README to "expect bugs with authentication", and
does not implement SSO. Home Assistant needs async I/O, so the client lives
here — the same shape the other integrations in this family use.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any

import aiohttp

from .auth import SomtodayAuth, SomtodayInvalidAuth

_LOGGER = logging.getLogger(__name__)

# Every list endpoint wraps its payload in ``{"items": [...]}``.
_ITEMS = "items"

# Somtoday answers a paginated list with 206 Partial Content. Treating that as
# a failure is what it looks like when the integration cannot read anything at
# all, so both statuses are success here.
_SUCCESS_STATUSES = frozenset({200, 206})

# Items per page. The community documentation caps the grades endpoint at 100
# per request; the same ceiling is assumed for the rest.
_PAGE_SIZE = 100

# A hard stop, so a server that keeps claiming there is more can never spin
# this loop forever. 100 pages is far past any real school year.
_MAX_PAGES = 100

# ``Content-Range: items 0-99/250`` — the total is ``*`` when unknown.
_CONTENT_RANGE_RE = re.compile(r"items\s+(\d+)\s*-\s*(\d+)\s*/\s*(\d+|\*)")


def _parse_content_range(value: str | None) -> tuple[int, int | None] | None:
    """Return ``(end, total)`` from a ``Content-Range`` header, or ``None``.

    ``None`` means "no pagination to follow" — either the header is absent, or
    it is in a shape we do not recognise, in which case stopping after the
    current page is the safe reading.
    """
    if not value:
        return None
    match = _CONTENT_RANGE_RE.search(value)
    if not match:
        return None
    total = match.group(3)
    return int(match.group(2)), None if total == "*" else int(total)


class SomtodayApiError(Exception):
    """A Somtoday API call failed in a way that is worth retrying."""

    def __init__(self, status: int, message: str | None = None) -> None:
        """Initialise with the HTTP status that caused the failure."""
        super().__init__(message or f"Somtoday API request failed with status {status}")
        self.status = status


class SomtodayApiClient:
    """Read-only client for the Somtoday student REST API."""

    def __init__(self, session: aiohttp.ClientSession, auth: SomtodayAuth) -> None:
        """Initialise the client with a session and an authenticated token holder."""
        self._session = session
        self._auth = auth

    @property
    def auth(self) -> SomtodayAuth:
        """Return the token holder backing this client."""
        return self._auth

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def async_get_account(self) -> dict[str, Any]:
        """Return the account object for the signed-in user.

        Used only to derive a stable unique id for the config entry. Callers
        must tolerate an empty dict: the endpoint is undocumented enough that a
        failure here should not block setup.
        """
        try:
            data, _headers = await self._async_get("/rest/v1/account/me")
            return data
        except (SomtodayApiError, aiohttp.ClientError):
            _LOGGER.debug("Could not read the Somtoday account object", exc_info=True)
            return {}

    async def async_get_students(self) -> list[dict[str, Any]]:
        """Return every student this account can see.

        A student account sees exactly itself; a parent account sees each of
        their children.
        """
        return await self._async_get_items("/rest/v1/leerlingen")

    async def async_get_appointments(
        self, start: date, end: date
    ) -> list[dict[str, Any]]:
        """Return the schedule appointments between two dates, inclusive.

        Fetched once for the whole account rather than per student: the
        appointments carry their students under ``additionalObjects.leerlingen``,
        so one call covers every child on a parent account.
        """
        return await self._async_get_items(
            "/rest/v1/afspraken",
            params=[
                ("begindatum", start.isoformat()),
                ("einddatum", end.isoformat()),
                ("sort", "asc-id"),
                ("additional", "vak"),
                ("additional", "docentAfkortingen"),
                ("additional", "leerlingen"),
            ],
        )

    async def async_get_homework(
        self, start: date, student_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Return study-guide items (homework and tests) from ``start`` onwards.

        Somtoday splits these over three endpoints depending on how the item is
        assigned — to a lesson, to a day, or to a week. All three are fetched
        and merged; skipping any of them silently loses homework.

        A failure on one variant does not sink the others: partial homework is
        far more useful than none, and the endpoints do fail independently.
        """
        params: list[tuple[str, str]] = [
            ("begintNaOfOp", start.isoformat()),
            ("additional", "huiswerkgemaakt"),
            ("additional", "swigemaaktVinkjes"),
            ("additional", "lesgroep"),
        ]
        if student_id is not None:
            params.append(
                ("geenDifferentiatieOfGedifferentieerdVoorLeerling", str(student_id))
            )

        items: list[dict[str, Any]] = []
        for endpoint in (
            "/rest/v1/studiewijzeritemafspraaktoekenningen",
            "/rest/v1/studiewijzeritemdagtoekenningen",
            "/rest/v1/studiewijzeritemweektoekenningen",
        ):
            try:
                page = await self._async_get_items(endpoint, params=params)
            except SomtodayInvalidAuth:
                raise
            except (SomtodayApiError, aiohttp.ClientError):
                _LOGGER.debug("Homework endpoint %s failed", endpoint, exc_info=True)
                continue
            items.extend(page)

        return items

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _async_get_items(
        self, path: str, params: list[tuple[str, str]] | None = None
    ) -> list[dict[str, Any]]:
        """Return every item from a list endpoint, following pagination.

        Somtoday paginates its list endpoints and answers ``206 Partial
        Content`` with a ``Content-Range: items <start>-<end>/<total>`` header.
        Reading only the first page silently loses data — a fortnight of
        lessons runs past a hundred appointments easily — so this walks the
        range until the server says there is nothing left.

        Endpoints that ignore the ``Range`` header simply answer ``200`` with
        everything, which ends the loop after one pass.
        """
        items: list[dict[str, Any]] = []
        start = 0

        for _ in range(_MAX_PAGES):
            data, headers = await self._async_get(path, params, start=start)
            page = data.get(_ITEMS) or []
            items.extend(page)

            # A page shorter than the one we asked for is the last one. This is
            # the primary stop condition on purpose: it holds whether or not the
            # server sends a usable Content-Range, and it cannot be talked into
            # looping by a header that never advances.
            if len(page) < _PAGE_SIZE:
                break

            span = _parse_content_range(headers.get("Content-Range"))
            if span is None:
                break
            end, total = span
            if total is not None and end + 1 >= total:
                break
            if end + 1 <= start:
                # A server that does not advance the range would loop forever.
                break
            start = end + 1
        else:
            _LOGGER.warning(
                "Stopped paging %s after %s pages; the list may be incomplete",
                path,
                _MAX_PAGES,
            )

        return items

    async def _async_get(
        self,
        path: str,
        params: list[tuple[str, str]] | None = None,
        start: int | None = None,
    ) -> tuple[dict[str, Any], Mapping[str, str]]:
        """GET a Somtoday endpoint, retrying once after a forced token refresh.

        Returns the parsed body together with the response headers, because
        pagination lives in ``Content-Range``.

        Raises:
            SomtodayInvalidAuth: The session is dead even after a refresh —
                the caller should start a reauth flow.
            SomtodayApiError: Any non-success status, or an unparseable body.
            aiohttp.ClientError: On network-level failures. Not caught here;
                ``DataUpdateCoordinator`` already wraps these.

        """
        status, data, headers = await self._async_request(path, params, start)

        if status == 401:
            # The access token was rejected mid-flight. Force a refresh and
            # try once more; a second 401 means the session is genuinely gone.
            _LOGGER.debug("Somtoday returned 401 for %s, refreshing token", path)
            await self._async_force_refresh()
            status, data, headers = await self._async_request(path, params, start)
            if status == 401:
                raise SomtodayInvalidAuth(
                    "Somtoday rejected the access token after a refresh"
                )

        if status == 403:
            # Permission denied is not a dead session — typically the account
            # simply cannot see this endpoint. Retryable, never reauth.
            raise SomtodayApiError(403, f"Somtoday denied access to {path}")

        # 206 is the normal answer for a paginated list here, not an exception.
        if status not in _SUCCESS_STATUSES:
            raise SomtodayApiError(status)

        if not isinstance(data, dict):
            raise SomtodayApiError(status, f"Somtoday returned an unexpected body for {path}")

        return data, headers

    async def _async_request(
        self, path: str, params: list[tuple[str, str]] | None, start: int | None = None
    ) -> tuple[int, Any, Mapping[str, str]]:
        """Perform one authenticated GET, returning status, body and headers."""
        token = await self._auth.async_get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            # Without this Somtoday happily answers with XML.
            "Accept": "application/json",
        }
        if start is not None:
            headers["Range"] = f"items={start}-{start + _PAGE_SIZE - 1}"
        url = f"{self._auth.api_url}{path}"

        async with self._session.get(url, params=params, headers=headers) as response:
            if response.status not in _SUCCESS_STATUSES:
                return response.status, None, response.headers
            try:
                body = await response.json(content_type=None)
            except ValueError as err:
                raise SomtodayApiError(
                    response.status, f"Somtoday returned a malformed body for {path}"
                ) from err
            return response.status, body, response.headers

    async def _async_force_refresh(self) -> None:
        """Expire the cached access token so the next call refreshes it."""
        self._auth.tokens.expires_at = datetime.now(timezone.utc)
        await self._auth.async_get_access_token()
