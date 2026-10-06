"""Push notification configs, created, read, listed and deleted over both protocol versions.

A config created without an ``id`` is stored under the task's id, and the
response says so.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.asyncio(loop_scope="module")]

WEBHOOK = "http://127.0.0.1:9/hook"


async def test_push_config_lifecycle_over_a2a_1_0(served) -> None:
    task_id = (await served.send("hello"))["result"]["task"]["id"]

    created = await served.rpc("CreateTaskPushNotificationConfig", {"taskId": task_id, "url": WEBHOOK})
    assert created["result"] == {"id": task_id, "taskId": task_id, "url": WEBHOOK}

    read = await served.rpc("GetTaskPushNotificationConfig", {"taskId": task_id, "id": task_id})
    assert read["result"] == created["result"]

    listed = await served.rpc("ListTaskPushNotificationConfigs", {"taskId": task_id})
    assert listed["result"]["configs"] == [created["result"]]

    deleted = await served.rpc("DeleteTaskPushNotificationConfig", {"taskId": task_id, "id": task_id})
    assert "error" not in deleted
    listed = await served.rpc("ListTaskPushNotificationConfigs", {"taskId": task_id})
    assert listed["result"].get("configs", []) == []


async def test_a_config_named_by_the_client_keeps_its_id(served) -> None:
    task_id = (await served.send("hello"))["result"]["task"]["id"]

    created = await served.rpc(
        "CreateTaskPushNotificationConfig", {"taskId": task_id, "id": "mine", "url": WEBHOOK}
    )

    assert created["result"]["id"] == "mine"


async def test_push_config_lifecycle_over_a2a_0_3(served) -> None:
    task_id = (await served.send_v03("hello"))["result"]["id"]

    created = await served.rpc(
        "tasks/pushNotificationConfig/set",
        {"taskId": task_id, "pushNotificationConfig": {"url": WEBHOOK}},
        version=None,
    )
    assert created["result"] == {"taskId": task_id, "pushNotificationConfig": {"id": task_id, "url": WEBHOOK}}

    read = await served.rpc(
        "tasks/pushNotificationConfig/get", {"id": task_id, "pushNotificationConfigId": task_id}, version=None
    )
    assert read["result"] == created["result"]

    listed = await served.rpc("tasks/pushNotificationConfig/list", {"id": task_id}, version=None)
    assert listed["result"] == [created["result"]]

    deleted = await served.rpc(
        "tasks/pushNotificationConfig/delete", {"id": task_id, "pushNotificationConfigId": task_id}, version=None
    )
    assert "error" not in deleted
    listed = await served.rpc("tasks/pushNotificationConfig/list", {"id": task_id}, version=None)
    assert listed["result"] == []
