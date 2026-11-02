"""Regression tests for the opt-in SignalR read-only diagnostic."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import WSMessage, WSMsgType
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest

from custom_components.remeha_home.api import RemehaHomeAPI
from custom_components.remeha_home.const import DOMAIN
from custom_components.remeha_home.signalr_probe import (
    RECORD_SEPARATOR,
    SIGNALR_HUB_URL,
    SignalRProbeError,
    _shape,
    _trusted_signalr_redirect,
    async_probe_signalr,
)
from custom_components.remeha_home.signalr_probe_service import SERVICE_PROBE_SIGNALR

from .conftest import APPLIANCE_ID, setup_integration


class FakeWebSocket:
    """Transport that answers with fixed SignalR frames and records writes."""

    def __init__(self, frames: list[str]) -> None:
        """Save frames that the simulated server will send."""
        self.frames = list(frames)
        self.sent: list[str] = []
        self.closed = False

    async def send_str(self, data: str) -> None:
        """Remember all outbound messages sent by the probe."""
        self.sent.append(data)

    async def receive(self, *, timeout: float) -> WSMessage:
        """Return the next fake frame or finish the listening period."""
        if not self.frames:
            raise TimeoutError
        return WSMessage(WSMsgType.TEXT, self.frames.pop(0), None)

    async def close(self) -> None:
        """Record closure of the test connection."""
        self.closed = True


def _api_and_session(frames: list[str]):
    api = MagicMock(spec=RemehaHomeAPI)
    api.async_negotiate_signalr.return_value = {
        "connectionToken": "SECRET_CONNECTION_TOKEN",
        "availableTransports": [{"transport": "WebSockets"}],
    }
    api.async_get_access_token.return_value = "SECRET_BEARER_TOKEN"
    ws = FakeWebSocket(frames)
    session = MagicMock()
    session.ws_connect = AsyncMock(return_value=ws)
    return api, session, ws


async def test_negotiate_uses_only_protocol_post() -> None:
    """Negotiating must not invoke any Remeha settings endpoint."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = AsyncMock(return_value={"connectionId": "opaque"})
    oauth = MagicMock()
    oauth.async_request = AsyncMock(return_value=response)

    result = await RemehaHomeAPI(oauth).async_negotiate_signalr()

    assert result == {"connectionId": "opaque"}
    oauth.async_request.assert_awaited_once()
    request = oauth.async_request.call_args
    assert request.args == (
        "POST",
        "https://api.bdrthermea.net/Mobile/api/homes/signalr/negotiate",
    )
    assert request.kwargs["params"] == {"negotiateVersion": 1}


def test_shape_does_not_return_any_values() -> None:
    """Return only field names and types, including JSON encoded in strings."""
    shape = _shape(
        {
            "roomTemperature": 20.5,
            "houseName": "PRIVATE HOME",
            "devices": [{"serialNumber": "SECRET_SERIAL"}],
            "nestedJson": '{"power":123,"token":"SECRET_BEARER_TOKEN"}',
        }
    )
    serialized = json.dumps(shape)
    assert "roomTemperature" in serialized
    assert "power" in serialized
    assert "PRIVATE HOME" not in serialized
    assert "SECRET_SERIAL" not in serialized
    assert "SECRET_BEARER_TOKEN" not in serialized
    assert "20.5" not in serialized


async def test_probe_only_sends_handshake_and_redacts_payloads() -> None:
    """Listen without invoking a hub method or exposing device data."""
    event = {
        "type": 1,
        "target": "DashboardUpdated",
        "arguments": [
            {
                "roomTemperature": 20.5,
                "houseName": "PRIVATE HOME",
                "token": "SECRET_BEARER_TOKEN",
            }
        ],
    }
    api, session, ws = _api_and_session(
        ["{}" + RECORD_SEPARATOR + json.dumps(event) + RECORD_SEPARATOR]
    )

    result = await async_probe_signalr(api, session, 5)

    assert result["connected"] is True
    assert result["handshake_acknowledged"] is True
    assert result["invocation_count"] == 1
    assert result["events"][0]["target"] == "DashboardUpdated"
    assert "roomTemperature" in json.dumps(result)
    assert "PRIVATE HOME" not in json.dumps(result)
    assert "SECRET_BEARER_TOKEN" not in json.dumps(result)
    assert "SECRET_CONNECTION_TOKEN" not in json.dumps(result)
    assert ws.sent == ['{"protocol":"json","version":1}' + RECORD_SEPARATOR]
    assert ws.closed
    api.async_negotiate_signalr.assert_awaited_once()
    api.async_get_access_token.assert_awaited_once()
    session.ws_connect.assert_awaited_once()
    call = session.ws_connect.call_args
    assert call.args == (SIGNALR_HUB_URL,)
    assert call.kwargs["params"]["id"] == "SECRET_CONNECTION_TOKEN"


async def test_probe_no_events_is_valid_result() -> None:
    """A successful handshake does not guarantee that the server sends events."""
    api, session, ws = _api_and_session(["{}" + RECORD_SEPARATOR])
    result = await async_probe_signalr(api, session, 5)
    assert result["events"] == []
    assert result["handshake_acknowledged"]
    assert ws.closed


async def test_probe_handles_partial_record_frames() -> None:
    """A record may be split between WebSocket frames."""
    event = json.dumps({"type": 1, "target": "StateChanged", "arguments": [True]})
    api, session, _ = _api_and_session(
        ["{}" + RECORD_SEPARATOR + event[:12], event[12:] + RECORD_SEPARATOR]
    )
    result = await async_probe_signalr(api, session, 5)
    assert result["invocation_count"] == 1
    assert result["events"][0]["arguments"]["first_item"] == "boolean"


async def test_probe_rejects_unverified_redirect_without_credentials() -> None:
    """Never send a bearer token to an arbitrary negotiate redirect."""
    api, session, _ = _api_and_session([])
    api.async_negotiate_signalr.return_value = {
        "url": "https://untrusted.example/signalr/",
        "accessToken": "SECRET_OTHER_TOKEN",
    }
    with pytest.raises(SignalRProbeError, match="redirect destination is not trusted"):
        await async_probe_signalr(api, session, 5)
    api.async_get_access_token.assert_not_awaited()
    session.ws_connect.assert_not_awaited()


async def test_probe_accepts_azure_redirect_with_only_redirect_credentials() -> None:
    """Follow a trusted Azure redirect without sending Remeha OAuth or BDR key."""
    api, session, ws = _api_and_session(["{}" + RECORD_SEPARATOR])
    url = "https://example.service.signalr.net/client/?hub=home&extra=1"
    api.async_negotiate_signalr.return_value = {
        "url": url,
        "accessToken": "SECRET_AZURE_REDIRECT_TOKEN",
    }

    result = await async_probe_signalr(api, session, 5)

    assert result["connected"]
    assert result["handshake_acknowledged"]
    assert "SECRET_AZURE_REDIRECT_TOKEN" not in json.dumps(result)
    assert ws.closed
    assert ws.sent == ['{"protocol":"json","version":1}' + RECORD_SEPARATOR]
    api.async_get_access_token.assert_not_awaited()
    call = session.ws_connect.call_args
    assert call.args == (url,)
    assert call.kwargs["headers"] == {
        "Authorization": "Bearer SECRET_AZURE_REDIRECT_TOKEN"
    }
    assert call.kwargs["params"] is None
    assert call.kwargs["allow_redirects"] is False


@pytest.mark.parametrize(
    "url",
    [
        "http://example.service.signalr.net/client/",
        "https://example.service.signalr.net.evil.example/client/",
        "https://example.service.signalr.net@evil.example/client/",
        "https://evil.example@sample.service.signalr.net/client/",
        "https://sample.service.signalr.net:8443/client/",
        "https://sample.service.signalr.net/admin/",
        "https://service.signalr.net/client/",
        "https://localhost/client/",
        "https://sample.service.signalr.net/client/#fragment",
        "not a url",
    ],
)
def test_probe_redirect_allowlist_rejects_untrusted_targets(url: str) -> None:
    """Do not open WebSockets to arbitrary hosts or unsafe service paths."""
    assert not _trusted_signalr_redirect(url)


async def test_probe_rejects_redirect_without_token() -> None:
    """Refuse unauthenticated redirects instead of forwarding Remeha tokens."""
    api, session, _ = _api_and_session([])
    api.async_negotiate_signalr.return_value = {
        "url": "https://sample.service.signalr.net/client/?hub=home"
    }
    with pytest.raises(SignalRProbeError, match="returned no access token"):
        await async_probe_signalr(api, session, 5)
    api.async_get_access_token.assert_not_awaited()
    session.ws_connect.assert_not_awaited()


async def test_probe_rejects_protocol_handshake_error_without_details() -> None:
    """Server-supplied error messages must not leak through service responses."""
    api, session, ws = _api_and_session(
        ['{"error":"SECRET_SERVER_DETAILS"}' + RECORD_SEPARATOR]
    )
    with pytest.raises(SignalRProbeError, match="handshake was rejected") as info:
        await async_probe_signalr(api, session, 5)
    assert "SECRET_SERVER_DETAILS" not in str(info.value)
    assert ws.closed


async def test_action_registers_without_connecting_at_startup(
    hass: HomeAssistant, config_entry, mock_api
) -> None:
    """A normal integration start must never open a SignalR connection."""
    with patch(
        "custom_components.remeha_home.signalr_probe_service.async_probe_signalr",
        new_callable=AsyncMock,
    ) as probe:
        await setup_integration(hass, config_entry, mock_api)
        assert hass.services.has_service(DOMAIN, SERVICE_PROBE_SIGNALR)
        probe.assert_not_awaited()

        appliance = dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, APPLIANCE_ID), config_entry.entry_id
        )
        assert appliance is not None
        probe.return_value = {"connected": True, "events": []}

        result = await hass.services.async_call(
            DOMAIN,
            SERVICE_PROBE_SIGNALR,
            {"device_id": appliance.id, "listen_seconds": 5},
            blocking=True,
            return_response=True,
        )
        assert result == {"connected": True, "events": []}
        probe.assert_awaited_once()
        mock_api.async_set_manual.assert_not_awaited()
        mock_api.async_set_heating_curve.assert_not_awaited()
        mock_api.async_set_dhw_comfort_setpoint.assert_not_awaited()
