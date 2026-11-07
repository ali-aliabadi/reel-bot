"""Optional text-editing Relay client for the independent status observer."""

import json
from typing import cast

from reel_bot import relay_api, web

STATUSES = ("queued", "sending", "delivered", "failed", "partially_delivered")


class ProgressRelay(relay_api.Relay):
    def delete_text(self, message_id: str) -> None:
        self._check_id(message_id)
        req = web.Request(
            "DELETE",
            f"{self.url}/v1/messages/{message_id}/text",
            "relay delete",
            b"",
            self._headers(),
            retry=False,
        )
        self.net.send(req)

    def supports_delete(self) -> bool:
        return self._capability("delete_text")

    def edit_text(self, message_id: str, text: str) -> None:
        """Replace this app's delivered text message without sending a new one."""
        self._check_id(message_id)
        url = f"{self.url}/v1/messages/{message_id}/text"
        body = json.dumps({"text": text}).encode()
        req = web.Request("PATCH", url, "relay edit", body, self._headers(), retry=False)
        self.net.send(req)

    def _check_id(self, message_id: str) -> None:
        if not message_id.startswith("msg_") or "/" in message_id:
            raise relay_api.RelayError("not a Relay message ID")

    def supports_edit(self) -> bool:
        """Old Relay versions have no edit_text capability; do not create stale cards."""
        return self._capability("edit_text")

    def _capability(self, name: str) -> bool:
        req = web.Request(
            "GET",
            f"{self.url}/v1/channels",
            "relay capabilities",
            b"",
            self._headers(),
            retry=False,
        )
        try:
            data: object = json.loads(self.net.send(req).body)
            channels = (
                cast(dict[str, object], data).get("channels") if isinstance(data, dict) else None
            )
            if not isinstance(channels, list):
                return False
            for value in cast(list[object], channels):
                if isinstance(value, dict):
                    channel = cast(dict[str, object], value)
                    if channel.get("name") == "telegram":
                        return channel.get(name) is True and channel.get("healthy") is True
        except (ValueError, RecursionError):
            return False
        return False

    def message_status(self, message_id: str) -> str:
        self._check_id(message_id)
        req = web.Request(
            "GET",
            f"{self.url}/v1/messages/{message_id}",
            "relay status",
            b"",
            self._headers(),
            retry=False,
        )
        try:
            data: object = json.loads(self.net.send(req).body)
            status = cast(dict[str, object], data).get("status") if isinstance(data, dict) else None
            if status in STATUSES:
                return str(status)
        except (ValueError, RecursionError):
            pass
        raise relay_api.RelayError("Relay returned no delivery status")
