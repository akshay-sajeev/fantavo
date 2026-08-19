"""Tests for ingest.espn_client.fetch_live_league -- the only module in
this repo that calls the real ESPN API from production code. Every test
here uses a fake Transport; none makes a real network call (CLAUDE.md's
"fixtures, not live calls" rule)."""

from __future__ import annotations

from typing import Any

import pytest
import requests

from ingest.espn_client import (
    EspnAuthenticationError,
    EspnFetchError,
    EspnLeagueNotFoundError,
    fetch_live_league,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _FakeTransport:
    """Records every call and returns a canned response keyed on whether
    this is the free-agent view request or the main league request --
    fetch_live_league makes exactly one of each."""

    def __init__(self, league_status: int = 200, league_payload: Any = None) -> None:
        self.league_status = league_status
        self.league_payload = league_payload if league_payload is not None else {"teams": []}
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        params = kwargs.get("params") or []
        is_free_agent_call = ("view", "kona_player_info") in params
        if is_free_agent_call:
            return _FakeResponse(200, {"players": [{"id": 1, "fullName": "Fake Player"}]})
        return _FakeResponse(self.league_status, self.league_payload)


def test_fetch_live_league_merges_league_and_free_agent_responses() -> None:
    transport = _FakeTransport(league_payload={"id": 999, "teams": [{"id": 1, "name": "A"}]})
    result = fetch_live_league(999, 2026, None, None, transport=transport)
    assert result["id"] == 999
    assert result["_freeAgents"] == [{"id": 1, "fullName": "Fake Player"}]


def test_fetch_live_league_sends_cookies_only_when_both_are_provided() -> None:
    transport = _FakeTransport()
    fetch_live_league(999, 2026, "s2-value", "swid-value", transport=transport)
    assert transport.calls[0]["cookies"] == {"espn_s2": "s2-value", "SWID": "swid-value"}


def test_fetch_live_league_sends_no_cookies_for_a_public_league() -> None:
    transport = _FakeTransport()
    fetch_live_league(999, 2026, None, None, transport=transport)
    assert transport.calls[0]["cookies"] == {}


def test_fetch_live_league_raises_authentication_error_on_401() -> None:
    transport = _FakeTransport(league_status=401)
    with pytest.raises(EspnAuthenticationError):
        fetch_live_league(999, 2026, "wrong", "wrong", transport=transport)


def test_fetch_live_league_raises_not_found_error_on_404() -> None:
    transport = _FakeTransport(league_status=404)
    with pytest.raises(EspnLeagueNotFoundError):
        fetch_live_league(123456789, 2026, None, None, transport=transport)


def test_fetch_live_league_raises_generic_error_on_other_failures() -> None:
    transport = _FakeTransport(league_status=500)
    with pytest.raises(EspnFetchError):
        fetch_live_league(999, 2026, None, None, transport=transport)


def test_fetch_live_league_wraps_network_errors() -> None:
    class _RaisingTransport:
        def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            raise requests.ConnectionError("no route to host")

    with pytest.raises(EspnFetchError):
        fetch_live_league(999, 2026, None, None, transport=_RaisingTransport())


def test_fetch_live_league_survives_a_failed_free_agent_call() -> None:
    class _PartialFailureTransport:
        def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            params = kwargs.get("params") or []
            if ("view", "kona_player_info") in params:
                return _FakeResponse(500, {})
            return _FakeResponse(200, {"id": 999, "teams": []})

    result = fetch_live_league(999, 2026, None, None, transport=_PartialFailureTransport())
    assert result["_freeAgents"] == []


def test_fetch_live_league_survives_a_raised_error_on_the_free_agent_call() -> None:
    class _PartialRaisingTransport:
        def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            params = kwargs.get("params") or []
            if ("view", "kona_player_info") in params:
                raise requests.ConnectionError("no route to host")
            return _FakeResponse(200, {"id": 999, "teams": []})

    result = fetch_live_league(999, 2026, None, None, transport=_PartialRaisingTransport())
    assert result["_freeAgents"] == []
