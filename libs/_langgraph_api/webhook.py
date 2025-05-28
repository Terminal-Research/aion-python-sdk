from datetime import UTC, datetime

import structlog

from langgraph_api.http import get_http_client, get_loopback_client
from langgraph_api.worker import WorkerResult

logger = structlog.stdlib.get_logger(__name__)


async def call_webhook(result: "WorkerResult") -> None:
    checkpoint = result["checkpoint"]
    payload = {
        **result["run"],
        "status": result["status"],
        "run_started_at": result["run_started_at"],
        "run_ended_at": result["run_ended_at"],
        "webhook_sent_at": datetime.now(UTC).isoformat(),
        "values": checkpoint["values"] if checkpoint else None,
    }
    if exception := result["exception"]:
        payload["error"] = str(exception)

    try:
        webhook = result["webhook"]
        if webhook.startswith("/"):
            # Call into this own app
            webhook_client = get_loopback_client()
        else:
            webhook_client = get_http_client()
        await webhook_client.post(webhook, json=payload, total_timeout=20)
        await logger.ainfo(
            "Background worker called webhook",
            webhook=result["webhook"],
            run_id=result["run"]["run_id"],
        )
    except Exception as exc:
        logger.exception(
            f"Background worker failed to call webhook {result['webhook']}",
            exc_info=exc,
            webhook=result["webhook"],
        )
