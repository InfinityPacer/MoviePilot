import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agent.tools.impl.update_subscribe import UpdateSubscribeTool


def test_update_subscribe_tool_emits_agent_update_scene_and_fields():
    """Agent 更新订阅后应广播 agent_update 场景和真实变更字段。"""
    old = SimpleNamespace(
        id=7,
        name="旧名",
        year="2026",
        type="电视剧",
        season=1,
        state="R",
        total_episode=12,
        lack_episode=3,
        start_episode=1,
        quality=None,
        resolution=None,
        effect=None,
        to_dict=lambda: {"id": 7, "name": "旧名", "state": "R"},
    )
    new = SimpleNamespace(
        id=7,
        name="新名",
        year="2026",
        type="电视剧",
        season=1,
        state="R",
        total_episode=12,
        lack_episode=3,
        start_episode=1,
        quality=None,
        resolution=None,
        effect=None,
        to_dict=lambda: {"id": 7, "name": "新名", "state": "R"},
    )
    oper = AsyncMock()
    oper.async_get.side_effect = [old, new]

    with patch("app.agent.tools.impl.update_subscribe.SubscribeOper", return_value=oper), patch(
        "app.agent.tools.impl.update_subscribe.eventmanager.async_send_event",
        new=AsyncMock(),
    ) as send_event:
        result = asyncio.run(UpdateSubscribeTool(session_id="s", user_id="u").run(subscribe_id=7, name="新名"))

    payload = send_event.await_args.args[1]
    assert payload["scene"] == "agent_update"
    assert payload["fields"] == ["name"]
    assert json.loads(result)["success"] is True
