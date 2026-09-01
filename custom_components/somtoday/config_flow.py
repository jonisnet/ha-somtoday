"""Config flow for the Somtoday integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .api import SomtodayApiClient, SomtodayApiError
from .auth import (
    SomtodayAuth,
    SomtodayAuthError,
    SomtodayInvalidAuth,
    SomtodayTokens,
    async_exchange_code,
    build_authorize_url,
    code_challenge_from_verifier,
    extract_code,
    generate_code_verifier,
    generate_state,
)
from .const import (
    CONF_API_URL,
    CONF_DAYS_AHEAD,
    CONF_PLANNER_ALLOWED_USERS,
    CONF_REDIRECT_URL,
    CONF_REFRESH_INTERVAL,
    CONF_REFRESH_TOKEN,
    CONF_TENANT,
    DAYS_AHEAD_OPTIONS,
    DEFAULT_DAYS_AHEAD,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
    REFRESH_INTERVAL_OPTIONS,
)
from .models import parse_student

_LOGGER = logging.getLogger(__name__)

STEP_SCHEMA = vol.Schema({vol.Required(CONF_REDIRECT_URL): str})

# Errors after which the authorization link is spent and a fresh one is needed:
# either the code reached Somtoday (and a code is single-use), or its fate is
# unknown. Everything else — a wrong URL, the login page copied too early —
# never got as far as sending a code, so the link the user already has open
# stays valid and must not be replaced under them.
_SPENT_LINK_ERRORS = frozenset(
    {"invalid_auth", "cannot_connect", "no_students", "unknown"}
)


class SomtodayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Somtoday config and reauth flows.

    Somtoday's OAuth2 client redirects to a custom URI scheme that Home
    Assistant cannot receive, so the user completes the login in their own
    browser and pastes the failed redirect URL back here. That is what makes
    every school work, SSO and MFA included, without Home Assistant ever
    handling a password.
    """

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the per-flow PKCE state."""
        self._code_verifier: str | None = None
        self._state: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial login step."""
        if user_input is None:
            return self._async_show_login_form("user")

        tokens, error = await self._async_tokens_from_input(user_input)
        if tokens is None:
            return self._async_show_login_form("user", error)

        identity, error = await self._async_identify(tokens)
        if identity is None:
            return self._async_show_login_form("user", error)

        unique_id, title = identity
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(title=title, data=_entry_data(tokens))

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after Somtoday rejected the stored session."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to sign in again and rebind the existing entry."""
        if user_input is None:
            return self._async_show_login_form("reauth_confirm")

        tokens, error = await self._async_tokens_from_input(user_input)
        if tokens is None:
            return self._async_show_login_form("reauth_confirm", error)

        identity, error = await self._async_identify(tokens)
        if identity is None:
            return self._async_show_login_form("reauth_confirm", error)

        unique_id, _title = identity
        # Signing in with a different Somtoday account must abort rather than
        # silently rebind this entry — the entities belong to the old account.
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_mismatch(reason="wrong_account")

        return self.async_update_reload_and_abort(
            self._get_reauth_entry(), data_updates=_entry_data(tokens)
        )

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _async_show_login_form(
        self, step_id: str, error: str | None = None
    ) -> ConfigFlowResult:
        """Show the login form, minting a new authorization URL when needed.

        The link is only regenerated when the previous one is genuinely spent:
        on the first render, and after an error that means a code was consumed
        or rejected. A paste that never got as far as a code — the wrong URL,
        the login page copied too early — leaves the verifier untouched, so the
        login the user already has open in their browser still works.

        Regenerating there would be actively harmful: the code they are about
        to get is bound to the challenge from the *old* link, so a new verifier
        turns a recoverable typo into a PKCE mismatch and a second full login.
        """
        if self._code_verifier is None or error in _SPENT_LINK_ERRORS:
            self._code_verifier = generate_code_verifier()
            self._state = generate_state()
        auth_url = build_authorize_url(
            code_challenge_from_verifier(self._code_verifier), self._state
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=STEP_SCHEMA,
            errors={"base": error} if error else None,
            description_placeholders={"auth_url": auth_url},
        )

    async def _async_tokens_from_input(
        self, user_input: dict[str, Any]
    ) -> tuple[SomtodayTokens | None, str | None]:
        """Turn the pasted redirect URL into a token set.

        Returns ``(tokens, None)`` on success and ``(None, error_key)``
        otherwise, so the caller can re-render the form with a translated
        message.
        """
        if not self._code_verifier:
            return None, "unknown"

        try:
            code = extract_code(user_input[CONF_REDIRECT_URL], self._state)
        except ValueError as err:
            reason = str(err)
            if reason in ("login_page", "state_mismatch"):
                return None, reason
            return None, "invalid_url"

        session = async_get_clientsession(self.hass)
        try:
            tokens = await async_exchange_code(session, code, self._code_verifier)
        except SomtodayInvalidAuth:
            return None, "invalid_auth"
        except SomtodayAuthError as err:
            # Logged at error level, not debug: this is the first request Home
            # Assistant makes to Somtoday itself — everything before it happened
            # in the user's browser — so it is where a DNS, firewall or outage
            # problem on the Home Assistant host first shows up. "Could not
            # reach Somtoday" alone gives nobody anything to act on, and asking
            # a user to turn on debug logging for an integration they have not
            # managed to add yet means editing configuration.yaml.
            # The message carries a status code, never the code or a token.
            _LOGGER.error(
                "Somtoday rejected or could not be reached during the token "
                "exchange: %s",
                err,
            )
            return None, "cannot_connect"

        return tokens, None

    async def _async_identify(
        self, tokens: SomtodayTokens
    ) -> tuple[tuple[str, str] | None, str | None]:
        """Return ``((unique_id, title), None)`` for a freshly obtained session.

        The account object gives the most stable id, but it is the least
        documented endpoint here, so the student list is used as a fallback
        rather than failing setup over it.
        """
        session = async_get_clientsession(self.hass)
        client = SomtodayApiClient(session, SomtodayAuth(session, tokens))

        try:
            account = await client.async_get_account()
            students = await client.async_get_students()
        except SomtodayInvalidAuth:
            return None, "invalid_auth"
        except (SomtodayApiError, SomtodayAuthError, aiohttp.ClientError) as err:
            # Distinct wording from the token-exchange failure on purpose: this
            # one means the sign-in itself worked and it is the data API that is
            # unreachable, which is a different host and a different fix.
            _LOGGER.error(
                "Signed in to Somtoday, but reading the account from %s failed: %s",
                tokens.api_url,
                err,
            )
            return None, "cannot_connect"

        parsed = [
            student for raw in students if (student := parse_student(raw)) is not None
        ]
        if not parsed:
            return None, "no_students"

        unique_id = _account_unique_id(account) or parsed[0].uuid
        names = ", ".join(student.full_name for student in parsed)
        return (unique_id, f"Somtoday ({names})"), None

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> SomtodayOptionsFlow:
        """Return the options flow handler."""
        return SomtodayOptionsFlow()


class SomtodayOptionsFlow(OptionsFlow):
    """Handle the Somtoday options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the poll interval and how far ahead the schedule is fetched."""
        if user_input is not None:
            # No update listener: reloading here makes a changed interval or
            # window take effect immediately, and combining a listener with a
            # reload is deprecated in Home Assistant.
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
            return self.async_create_entry(data=_normalise_options(user_input))

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_REFRESH_INTERVAL,
                        default=_stored_choice(
                            options,
                            CONF_REFRESH_INTERVAL,
                            DEFAULT_REFRESH_INTERVAL,
                            REFRESH_INTERVAL_OPTIONS,
                        ),
                    ): _int_selector(CONF_REFRESH_INTERVAL, REFRESH_INTERVAL_OPTIONS),
                    vol.Required(
                        CONF_DAYS_AHEAD,
                        default=_stored_choice(
                            options, CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD, DAYS_AHEAD_OPTIONS
                        ),
                    ): _int_selector(CONF_DAYS_AHEAD, DAYS_AHEAD_OPTIONS),
                    vol.Optional(
                        CONF_PLANNER_ALLOWED_USERS,
                        default=", ".join(
                            options.get(CONF_PLANNER_ALLOWED_USERS, [])
                        ),
                    ): TextSelector(TextSelectorConfig()),
                }
            ),
        )


def _stored_choice(
    options: Mapping[str, Any],
    key: str,
    default: int,
    allowed: tuple[int, ...],
) -> str:
    """Return the stored value for a dropdown, or the default if it is gone.

    A value that is no longer offered would render the dropdown blank, which
    looks like the setting was lost. That happens whenever the available
    choices change between versions — the four-week window replaced the old
    30-day one — so an entry configured before the change falls back to the
    current default instead.
    """
    stored = options.get(key, default)
    return str(stored if stored in allowed else default)


def _int_selector(key: str, options: tuple[int, ...]) -> SelectSelector:
    """Return a translated dropdown for a fixed set of integer choices."""
    return SelectSelector(
        SelectSelectorConfig(
            options=[str(option) for option in options],
            mode=SelectSelectorMode.DROPDOWN,
            translation_key=key,
        )
    )


def _normalise_options(user_input: dict[str, Any]) -> dict[str, Any]:
    """Turn the dropdown's string values back into integers."""
    return {
        key: (
            int(value)
            if key in (CONF_REFRESH_INTERVAL, CONF_DAYS_AHEAD)
            else [item.strip() for item in value.split(",") if item.strip()]
        )
        for key, value in user_input.items()
    }


def _entry_data(tokens: SomtodayTokens) -> dict[str, Any]:
    """Return what gets persisted on the config entry.

    Only the refresh token is stored. Access tokens live an hour and are
    re-minted on every setup, so keeping one on disk would add risk without
    adding value.
    """
    return {
        CONF_REFRESH_TOKEN: tokens.refresh_token,
        CONF_API_URL: tokens.api_url,
        CONF_TENANT: tokens.tenant,
    }


def _account_unique_id(account: dict[str, Any]) -> str | None:
    """Return a stable id for the signed-in account, if one is available."""
    for link in account.get("links") or []:
        if isinstance(link, dict) and link.get("rel") == "self" and link.get("id"):
            return str(link["id"])
    return None
