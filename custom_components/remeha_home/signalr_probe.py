"""Opt-in, read-only SignalR protocol probe; never log or return event values."""

from __future__ import annotations

import asyncio
from collections import Counter
import json
import re
from time import monotonic
from typing import Any

from aiohttp import ClientSession, WSMsgType

from .api import BDR_SUBSCRIPTION_KEY, RemehaHomeAPI

SIGNALR_HUB_URL = "https://api.bdrthermea.net/Mobile/api/homes/signalr/"
RECORD_SEPARATOR = "\x1e"
MAX_EVENTS = 20
MAX_MESSAGE_BUFFER = 65536
_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,79}$")


class SignalRProbeError(Exception):
    """A safe diagnostic error, without credentials or arbitrary server text."""


def _shape(value: Any, depth: int = 0) -> Any:
    """Describe payload structure without returning personal or secret values."""
    if depth >= 5:
        return "nested"
    if isinstance(value, dict):
        fields = {}
        for key, nested in list(value.items())[:30]:
            key = str(key)
            if not _SAFE_NAME.fullmatch(key):
                key = "<redacted-key>"
            fields[key] = _shape(nested, depth + 1)
        return {
            "type": "object",
            "fields": fields,
            "truncated": len(value) > 30,
        }
    if isinstance(value, list):
        return {
            "type": "array",
            "count": len(value),
            "first_item": _shape(value[0], depth + 1) if value else None,
        }
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        # Some hubs wrap their JSON payload in a string argument.
        if value[:1] in ("{", "[") and len(value) <= MAX_MESSAGE_BUFFER:
            try:
                return _shape(json.loads(value), depth + 1)
            except (ValueError, RecursionError):
                pass
        return "string"
    return "unknown"


def _safe_target(target: Any) -> str:
    """Only expose protocol method names, not arbitrary strings or identifiers."""
    return target if isinstance(target, str) and _SAFE_NAME.fullmatch(target) else "<redacted>"


def _collect_record(
    record: str, summary: dict[str, Any], counts: Counter[int]
) -> None:
    """Parse a SignalR JSON hub record and retain metadata only."""
    try:
        message = json.loads(record)
    except (ValueError, RecursionError):
        summary["invalid_messages"] += 1
        return

    if not isinstance(message, dict):
        summary["invalid_messages"] += 1
        return

    if not summary["handshake_acknowledged"]:
        if "error" in message:
            raise SignalRProbeError("SignalR handshake was rejected")
        # The protocol handshake response is an empty object.
        if not message:
            summary["handshake_acknowledged"] = True
            return

    msg_type = message.get("type")
    if not isinstance(msg_type, int) or isinstance(msg_type, bool):
        summary["other_messages"] += 1
        return

    counts[msg_type] += 1
    if msg_type == 1:
        summary["invocation_count"] += 1
        if len(summary["events"]) < MAX_EVENTS:
            summary["events"].append(
                {
                    "target": _safe_target(message.get("target")),
                    "arguments": _shape(message.get("arguments")),
                }
            )
    if msg_type == 7:
        summary["closed_by_server"] = True


async def async_probe_signalr(
    api: RemehaHomeAPI, session: ClientSession, listen_seconds: int
) -> dict[str, Any]:
    """Listen temporarily to a hub without invoking any server-side methods.

    POST /negotiate and the protocol handshake establish a connection only.
    There are no climate, DHW, or appliance setting calls.
    """
    negotiate = await asyncio.wait_for(api.async_negotiate_signalr(), timeout=10)
    if not isinstance(negotiate, dict):
        raise SignalRProbeError("Unexpected SignalR negotiation response")
    if "error" in negotiate:
        raise SignalRProbeError("SignalR negotiation was rejected")
    if "url" in negotiate or "accessToken" in negotiate:
        # No forwarding credentials to an unverified redirect destination.
        raise SignalRProbeError("SignalR redirect is not supported by this diagnostic")

    transports = negotiate.get("availableTransports")
    if isinstance(transports, list) and not any(
        isinstance(item, dict) and item.get("transport") == "WebSockets"
        for item in transports
    ):
        raise SignalRProbeError("SignalR server did not offer WebSockets")

    connection_token = negotiate.get("connectionToken") or negotiate.get("connectionId")
    if not isinstance(connection_token, str) or not connection_token:
        raise SignalRProbeError("SignalR negotiation returned no connection token")

    bearer_token = await api.async_get_access_token()
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Ocp-Apim-Subscription-Key": BDR_SUBSCRIPTION_KEY,
    }
    # The connection token is sensitive: never include the URL or exceptions in
    # action responses or logs. This URL is deliberately fixed to Remeha's host.
    ws = await asyncio.wait_for(
        session.ws_connect(
            SIGNALR_HUB_URL,
            params={"id": connection_token},
            headers=headers,
            max_msg_size=MAX_MESSAGE_BUFFER,
            allow_redirects=False,
        ),
        timeout=10,
    )

    summary: dict[str, Any] = {
        "connected": True,
        "handshake_acknowledged": False,
        "listen_seconds": listen_seconds,
        "invocation_count": 0,
        "invalid_messages": 0,
        "other_messages": 0,
        "closed_by_server": False,
        "events": [],
    }
    counts: Counter[int] = Counter()
    buffer = ""

    try:
        await ws.send_str('{"protocol":"json","version":1}' + RECORD_SEPARATOR)
        deadline = monotonic() + listen_seconds
        while (remaining := deadline - monotonic()) > 0:
            try:
                message = await ws.receive(timeout=remaining)
            except TimeoutError:
                break

            if message.type == WSMsgType.TEXT:
                buffer += message.data
                if len(buffer) > MAX_MESSAGE_BUFFER:
                    raise SignalRProbeError("SignalR message exceeded diagnostic limit")
                while RECORD_SEPARATOR in buffer:
                    record, buffer = buffer.split(RECORD_SEPARATOR, 1)
                    if record:
                        _collect_record(record, summary, counts)
            elif message.type in (WSMsgType.CLOSED, WSMsgType.CLOSE, WSMsgType.CLOSING):
                summary["closed_by_server"] = True
                break
            elif message.type == WSMsgType.ERROR:
                raise SignalRProbeError("SignalR WebSocket reported a transport error")
            elif message.type == WSMsgType.BINARY:
                summary["other_messages"] += 1
    finally:
        await ws.close()

    summary["message_type_counts"] = {str(k): v for k, v in sorted(counts.items())}
    summary["note"] = "Only event names and field types are returned; no raw values or tokens"
    return summary
