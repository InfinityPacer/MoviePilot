import asyncio
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from app.api.endpoints.subscribe import create_subscribe, reset_subscribes, update_subscribe, update_subscribe_status
from app.schemas.subscribe import Subscribe
from app.schemas.types import EventType, MediaType


class SubscribeEndpointTest(TestCase):
    """
    订阅接口回归测试。
    """

    @staticmethod
    def _orm_subscribe(**kwargs):
        """构造接口测试用订阅 ORM 替身。"""
        data = {
            "id": 7,
            "name": "旧名",
            "year": "2026",
            "type": MediaType.TV.value,
            "season": 1,
            "state": "R",
            "total_episode": 12,
            "lack_episode": 3,
            "start_episode": 1,
            "note": [],
            "episode_priority": {},
            "current_priority": None,
        }
        data.update(kwargs)
        sub = SimpleNamespace(**data)

        def to_dict():
            return dict(data)

        async def async_update(_db, payload):
            data.update(payload)
            for key, value in payload.items():
                setattr(sub, key, value)

        sub.to_dict = to_dict
        sub.async_update = async_update
        return sub

    def test_create_subscribe_excludes_completed_episode_from_write_payload(self):
        """
        新增订阅时不应把 completed_episode 派生字段传入持久化链路。
        """
        subscribe_in = Subscribe(
            name="测试剧集",
            year="2026",
            type=MediaType.TV.value,
            season=1,
            total_episode=10,
            lack_episode=3,
        )

        self.assertEqual(subscribe_in.completed_episode, 7)

        with patch(
            "app.api.endpoints.subscribe.SubscribeChain.async_add",
            new=AsyncMock(return_value=(1, "新增订阅成功")),
        ) as async_add:
            response = asyncio.run(
                create_subscribe(
                    subscribe_in=subscribe_in,
                    current_user=SimpleNamespace(name="moviepilot-user"),
                )
            )

        self.assertTrue(response.success)
        self.assertNotIn("completed_episode", async_add.await_args.kwargs)
        self.assertEqual(async_add.await_args.kwargs["username"], "moviepilot-user")

    def test_create_subscribe_preserves_special_season_zero_with_doubanid(self):
        """
        新增订阅带豆瓣 ID 且显式指定 S0 时，标题规整不应覆盖调用方传入的季号。
        """
        subscribe_in = Subscribe(
            name="测试剧集",
            year="2026",
            type=MediaType.TV.value,
            doubanid="12345",
            season=0,
            total_episode=5,
            lack_episode=5,
        )

        with patch(
            "app.api.endpoints.subscribe.MetaInfo",
            return_value=SimpleNamespace(name="测试剧集", begin_season=None),
        ), patch(
            "app.api.endpoints.subscribe.SubscribeChain.async_add",
            new=AsyncMock(return_value=(1, "新增订阅成功")),
        ) as async_add:
            response = asyncio.run(
                create_subscribe(
                    subscribe_in=subscribe_in,
                    current_user=SimpleNamespace(name="moviepilot-user"),
                )
            )

        self.assertTrue(response.success)
        self.assertEqual(async_add.await_args.kwargs["season"], 0)

    def test_subscribe_modified_event_data_reports_true_changed_fields(self):
        from app.schemas.event import SubscribeModifiedEventData

        payload = SubscribeModifiedEventData(
            subscribe_id=7,
            old_subscribe_info={"name": "A", "note": [], "episode_priority": {}},
            subscribe_info={"name": "B", "note": [], "episode_priority": {"1": 100}},
            scene="reset",
        ).to_dict()

        self.assertEqual(payload["scene"], "reset")
        self.assertEqual(payload["fields"], ["episode_priority", "name"])

    def test_subscribe_modified_event_data_distinguishes_missing_and_none(self):
        from app.schemas.event import SubscribeModifiedEventData

        payload = SubscribeModifiedEventData(
            subscribe_id=7,
            old_subscribe_info={"name": None},
            subscribe_info={},
            scene="update",
        ).to_dict()

        self.assertEqual(payload["fields"], ["name"])

    def test_subscribe_modified_event_data_distinguishes_added_none_from_missing(self):
        from app.schemas.event import SubscribeModifiedEventData

        payload = SubscribeModifiedEventData(
            subscribe_id=7,
            old_subscribe_info={},
            subscribe_info={"name": None},
            scene="update",
        ).to_dict()

        self.assertEqual(payload["fields"], ["name"])

    def test_subscribe_modified_event_data_reset_reports_only_real_changes(self):
        from app.schemas.event import SubscribeModifiedEventData

        payload = SubscribeModifiedEventData(
            subscribe_id=7,
            old_subscribe_info={
                "note": [],
                "lack_episode": 3,
                "current_priority": None,
                "episode_priority": {},
                "state": "R",
            },
            subscribe_info={
                "note": [],
                "lack_episode": 3,
                "current_priority": None,
                "episode_priority": {"1": 100},
                "state": "R",
            },
            scene="reset",
        ).to_dict()

        self.assertEqual(payload["fields"], ["episode_priority"])

    def test_subscribe_modified_event_data_keeps_explicit_empty_fields(self):
        from app.schemas.event import SubscribeModifiedEventData

        payload = SubscribeModifiedEventData(
            subscribe_id=7,
            old_subscribe_info={"name": "A"},
            subscribe_info={"name": "B"},
            scene="update",
            fields=[],
        ).to_dict()

        self.assertEqual(payload["fields"], [])

    def test_update_subscribe_emits_update_scene_and_fields(self):
        old = self._orm_subscribe(id=7, name="旧名", state="R")
        updated = self._orm_subscribe(id=7, name="新名", state="R")
        subscribe_in = Subscribe(id=7, name="新名", type=MediaType.TV.value, season=1, lack_episode=3)

        with patch(
            "app.api.endpoints.subscribe.Subscribe.async_get",
            new=AsyncMock(side_effect=[old, updated]),
        ), patch(
            "app.api.endpoints.subscribe.eventmanager.async_send_event",
            new=AsyncMock(),
        ) as send_event:
            response = asyncio.run(update_subscribe(subscribe_in=subscribe_in, db=AsyncMock()))

        self.assertTrue(response.success)
        payload = send_event.await_args.args[1]
        self.assertEqual(send_event.await_args.args[0], EventType.SubscribeModified)
        self.assertEqual(payload["scene"], "update")
        self.assertEqual(payload["fields"], ["name"])

    def test_update_subscribe_status_emits_status_scene_and_state_field(self):
        old = self._orm_subscribe(id=7, state="S")
        updated = self._orm_subscribe(id=7, state="R")

        with patch(
            "app.api.endpoints.subscribe.Subscribe.async_get",
            new=AsyncMock(side_effect=[old, updated]),
        ), patch(
            "app.api.endpoints.subscribe.eventmanager.async_send_event",
            new=AsyncMock(),
        ) as send_event:
            response = asyncio.run(update_subscribe_status(subid=7, state="R", db=AsyncMock()))

        self.assertTrue(response.success)
        payload = send_event.await_args.args[1]
        self.assertEqual(payload["scene"], "status")
        self.assertEqual(payload["fields"], ["state"])

    def test_reset_subscribes_emits_reset_scene_and_only_changed_fields(self):
        old = self._orm_subscribe(
            id=7,
            note=[],
            lack_episode=12,
            current_priority=None,
            episode_priority={},
            state="R",
            total_episode=12,
        )
        updated = self._orm_subscribe(
            id=7,
            note=[],
            lack_episode=12,
            current_priority=None,
            episode_priority={"1": 100},
            state="R",
            total_episode=12,
        )

        with patch(
            "app.api.endpoints.subscribe.Subscribe.async_get",
            new=AsyncMock(side_effect=[old, updated]),
        ), patch(
            "app.api.endpoints.subscribe.eventmanager.async_send_event",
            new=AsyncMock(),
        ) as send_event:
            response = asyncio.run(reset_subscribes(subid=7, db=AsyncMock()))

        self.assertTrue(response.success)
        payload = send_event.await_args.args[1]
        self.assertEqual(payload["scene"], "reset")
        self.assertEqual(payload["fields"], ["episode_priority"])
