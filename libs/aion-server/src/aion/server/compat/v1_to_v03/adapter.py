from typing import Any

from aion.shared.logging import get_logger

logger = get_logger()


class A2AV03Adapter:
    # v1.0 PascalCase > v0.3 namespace/method
    _METHOD_MAP: dict[str, str] = {
        "SendMessage": "message/send",
        "SendStreamingMessage": "message/stream",
        "GetTask": "tasks/get",
        "CancelTask": "tasks/cancel",
        "SubscribeToTask": "tasks/resubscribe",
        "CreatePushNotificationConfig": "tasks/pushNotificationConfig/set",
        "GetPushNotificationConfig": "tasks/pushNotificationConfig/get",
        "ListPushNotificationConfigs": "tasks/pushNotificationConfig/list",
        "DeletePushNotificationConfig": "tasks/pushNotificationConfig/delete",
        "GetExtendedAgentCard": "agent/getAuthenticatedExtendedCard",
    }

    # v1.0 ROLE_* > v0.3 lowercase
    _ROLE_MAP: dict[str, str] = {
        "ROLE_USER": "user",
        "ROLE_AGENT": "agent",
    }

    # -- Parts ---------------------------------------------------------------

    @classmethod
    def _transform_file_part(cls, part: dict) -> dict:
        """Wrap flat v1.0 file fields back into a nested FilePart.file object.

        raw > bytes, url > uri, mediaType > mimeType, filename > name.
        """
        # {"raw":"…","mediaType":"…","filename":"…"} > {"kind":"file","file":{"bytes":"…","mimeType":"…","name":"…"}}
        # {"url":"…","mediaType":"…"}                > {"kind":"file","file":{"uri":"…","mimeType":"…"}}
        file_obj: dict = {}
        if "raw" in part:
            file_obj["bytes"] = part["raw"]
        if "url" in part:
            file_obj["uri"] = part["url"]
        if "mediaType" in part:
            file_obj["mimeType"] = part["mediaType"]
        if "filename" in part:
            file_obj["name"] = part["filename"]
        result: dict = {"kind": "file", "file": file_obj}
        if "metadata" in part:
            result["metadata"] = part["metadata"]
        return result

    @classmethod
    def _transform_part(cls, part: Any) -> Any:
        """Add kind to a single v1.0 Part dict. Routes file parts to _transform_file_part."""
        if not isinstance(part, dict):
            return part
        if "text" in part:
            return {"kind": "text", **part}
        if "data" in part:
            return {"kind": "data", **part}
        if "raw" in part or "url" in part:
            return cls._transform_file_part(part)
        return part  # unknown shape — pass through

    @classmethod
    def _transform_parts(cls, parts: Any) -> Any:
        """Apply _transform_part to every item in a parts list."""
        if not isinstance(parts, list):
            return parts
        return [cls._transform_part(p) for p in parts]

    # -- Message -------------------------------------------------------------

    @classmethod
    def _transform_message(cls, msg: dict) -> dict:
        """Add kind:"message", downgrade role, and transform parts."""
        result = {"kind": "message", **msg}
        if isinstance(result.get("role"), str):
            result["role"] = cls._ROLE_MAP.get(result["role"], result["role"])
        if isinstance(result.get("parts"), list):
            result["parts"] = cls._transform_parts(result["parts"])
        return result

    # -- Request -------------------------------------------------------------

    @classmethod
    def transform_request(cls, data: dict) -> dict:
        """Transform a JSON-RPC request dict from v1.0 to v0.3 wire format."""
        if isinstance(data.get("method"), str):
            data = {**data, "method": cls._METHOD_MAP.get(data["method"], data["method"])}

        params = data.get("params")
        if isinstance(params, dict) and isinstance(params.get("message"), dict):
            data = {**data, "params": {**params, "message": cls._transform_message(params["message"])}}

        logger.debug("a2a compat request (v1>v03): %s", data)
        return data
