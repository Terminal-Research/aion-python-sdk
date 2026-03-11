# Shim: rewrites a2a v0.3 JSON wire format to v1.0 before it leaves the server.
#
# v0.3 > v1.0 mapping:
#
#   Parts
#     {"kind":"text","text":"…"}                             > {"text":"…"}
#     {"kind":"data","data":{…}}                             > {"data":{…}}
#     {"kind":"file","file":{"bytes":"…","mimeType":"…",…}}  > {"raw":"…","mediaType":"…",…}
#     {"kind":"file","file":{"uri":"…","mimeType":"…"}}      > {"url":"…","mediaType":"…"}
#
#   Streaming events (result field)
#     {"kind":"status-update",  …} > {"statusUpdate":  {…}}
#     {"kind":"artifact-update",…} > {"artifactUpdate":{…}}
#
#   Other objects
#     Message.kind stripped; Task.kind stripped + history/artifacts recursed
#
# To remove when upgrading a2a-sdk to >= 1.0:
#   1. Delete src/aion/server/compat/
#   2. Remove AionA2AFastAPIApplication._create_response() from core/app/a2a_fastapi.py
#   3. Remove AionA2AFastAPIApplication._handle_get_agent_card() from core/app/a2a_fastapi.py
#   4. Remove AION_A2A_COMPAT_ENABLED from environment / deployment configs
#
# AION_A2A_COMPAT_ENABLED=true   (default) — transformation active
# AION_A2A_COMPAT_ENABLED=false            — pass-through, for debugging

import json
import os
from typing import Any

from a2a.types import TaskState
from aion.shared.logging import get_logger

logger = get_logger()


class A2AV1Adapter:
    enabled: bool = os.getenv("AION_A2A_COMPAT_ENABLED", "true").lower() != "false"

    _EVENT_WRAPPERS: dict[str, str] = {
        "status-update": "statusUpdate",
        "artifact-update": "artifactUpdate",
    }

    # v0.3 kebab-case > v1.0 SCREAMING_SNAKE_CASE
    # https://a2a-protocol.org/latest/specification/#413-taskstate
    _TASK_STATE_MAP: dict[str, str] = {
        TaskState.unknown.value: "TASK_STATE_UNSPECIFIED",
        TaskState.submitted.value: "TASK_STATE_SUBMITTED",
        TaskState.working.value: "TASK_STATE_WORKING",
        TaskState.completed.value: "TASK_STATE_COMPLETED",
        TaskState.failed.value: "TASK_STATE_FAILED",
        TaskState.canceled.value: "TASK_STATE_CANCELED",
        TaskState.input_required.value: "TASK_STATE_INPUT_REQUIRED",
        TaskState.rejected.value: "TASK_STATE_REJECTED",
        TaskState.auth_required.value: "TASK_STATE_AUTH_REQUIRED",
    }

    # -- Parts ---------------------------------------------------------------

    @classmethod
    def _transform_file_part(cls, part: dict) -> dict:
        """Flatten nested FilePart.file object and rename fields to v1.0 names.

        bytes > raw, uri > url, mimeType > mediaType, name > filename.
        """
        # {"kind":"file","file":{"bytes":"…","mimeType":"…","name":"…"}} > {"raw":"…","mediaType":"…","filename":"…"}
        # {"kind":"file","file":{"uri":"…","mimeType":"…"}}              > {"url":"…","mediaType":"…"}
        file_obj = part.get("file", {})
        result: dict = {}
        if "bytes" in file_obj:
            result["raw"] = file_obj["bytes"]
        if "uri" in file_obj:
            result["url"] = file_obj["uri"]
        if "mimeType" in file_obj:
            result["mediaType"] = file_obj["mimeType"]
        if "name" in file_obj:
            result["filename"] = file_obj["name"]
        if "metadata" in part:
            result["metadata"] = part["metadata"]
        return result

    @classmethod
    def _transform_part(cls, part: Any) -> Any:
        """Strip kind from a single Part dict. Routes FilePart to _transform_file_part."""
        if not isinstance(part, dict):
            return part
        kind = part.get("kind")
        if kind in ("text", "data"):
            return {k: v for k, v in part.items() if k != "kind"}
        if kind == "file":
            return cls._transform_file_part(part)
        return part  # unknown kind — pass through

    @classmethod
    def _transform_parts(cls, parts: Any) -> Any:
        """Apply _transform_part to every item in a parts list."""
        if not isinstance(parts, list):
            return parts
        return [cls._transform_part(p) for p in parts]

    # -- Nested objects ------------------------------------------------------

    @classmethod
    def _transform_task_status(cls, status: dict) -> dict:
        """Map state to TASK_STATE_* and transform the nested message if present."""
        result = dict(status)
        if "state" in result:
            result["state"] = cls._TASK_STATE_MAP.get(result["state"], result["state"])
        if isinstance(result.get("message"), dict):
            result["message"] = cls._transform_message(result["message"])
        return result

    @classmethod
    def _transform_message(cls, msg: dict) -> dict:
        """Strip kind from a Message and transform its parts."""
        result = {k: v for k, v in msg.items() if k != "kind"}
        if isinstance(result.get("parts"), list):
            result["parts"] = cls._transform_parts(result["parts"])
        return result

    @classmethod
    def _transform_artifact(cls, artifact: dict) -> dict:
        """Transform parts inside an Artifact. The Artifact object itself has no kind."""
        if not isinstance(artifact.get("parts"), list):
            return artifact
        return {**artifact, "parts": cls._transform_parts(artifact["parts"])}

    @classmethod
    def _transform_task(cls, task: dict) -> dict:
        """Strip kind from a Task and recurse into status, history, and artifacts."""
        result = {k: v for k, v in task.items() if k != "kind"}
        if isinstance(result.get("status"), dict):
            result["status"] = cls._transform_task_status(result["status"])
        if isinstance(result.get("history"), list):
            result["history"] = [
                cls._transform_message(m) if isinstance(m, dict) else m
                for m in result["history"]
            ]
        if isinstance(result.get("artifacts"), list):
            result["artifacts"] = [
                cls._transform_artifact(a) if isinstance(a, dict) else a
                for a in result["artifacts"]
            ]
        return result

    # -- Event level ---------------------------------------------------------

    @classmethod
    def _transform_event_inner(cls, inner: dict) -> dict:
        """Recurse into known nested fields of a streaming event (artifact, status)."""
        result = {}
        for k, v in inner.items():
            if k == "artifact" and isinstance(v, dict):
                result[k] = cls._transform_artifact(v)
            elif k == "status" and isinstance(v, dict):
                result[k] = cls._transform_task_status(v)
            else:
                result[k] = v
        return result

    @classmethod
    def _transform_result(cls, result: Any) -> Any:
        """Entry point for result-level transformation.

        Wraps streaming events under their v1.0 key (statusUpdate / artifactUpdate),
        strips kind from Task, and recurses into parts for everything else.
        """
        if not isinstance(result, dict):
            return result
        kind = result.get("kind")
        wrapper_key = cls._EVENT_WRAPPERS.get(kind)
        if wrapper_key:
            inner = {k: v for k, v in result.items() if k != "kind"}
            return {wrapper_key: cls._transform_event_inner(inner)}
        if kind == "task":
            return cls._transform_task(result)
        if isinstance(result.get("parts"), list):
            return {**result, "parts": cls._transform_parts(result["parts"])}
        return result

    @classmethod
    def transform_sse_event(cls, event_json: str) -> str:
        """Transform a JSON-RPC SSE event string from v0.3 to v1.0 wire format."""
        if not cls.enabled:
            return event_json

        data = json.loads(event_json)
        if isinstance(data, dict) and isinstance(data.get("result"), dict):
            data = {**data, "result": cls._transform_result(data["result"])}
        result = json.dumps(data, ensure_ascii=False)
        logger.debug("a2a compat SSE event: %s", result)
        return result

    @classmethod
    def transform_response(cls, data: dict) -> dict:
        """Transform a JSON-RPC response dict from v0.3 to v1.0 wire format."""
        if not cls.enabled:
            return data

        if isinstance(data.get("result"), dict):
            data = {**data, "result": cls._transform_result(data["result"])}
        logger.debug("a2a compat response: %s", json.dumps(data, ensure_ascii=False))
        return data

    @classmethod
    def transform_agent_card_response(cls, data: dict) -> dict:
        """Transform an Agent Card response dict from v0.3 to v1.0 wire format."""
        if not cls.enabled:
            return data

        data.pop("protocolVersion", None)
        data["protocolVersions"] = ["1.0"]
        return data
