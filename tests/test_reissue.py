"""Unit tests for /reissue_all command and list_users_with_inbounds."""
import os
import sys

os.environ.update({
    "BOT_TOKEN": "0:AAtest",
    "ADMIN_IDS": "100",
    "PANEL_URL": "http://127.0.0.1:9",
    "PANEL_USER": "u",
    "PANEL_PASS": "p",
})

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import db as db_mod


@pytest_asyncio.fixture
async def tmp_db(tmp_path):
    path = str(tmp_path / "bot.db")
    db_mod.init_db_config(path, path + ".bak")
    await db_mod.init_db()
    yield


# ── DB: list_users_with_inbounds ──────────────────────────────────────────────

class TestListUsersWithInbounds:
    @pytest.mark.asyncio
    async def test_empty(self, tmp_db):
        assert await db_mod.list_users_with_inbounds() == []

    @pytest.mark.asyncio
    async def test_returns_users_with_inbounds_only(self, tmp_db):
        await db_mod.upsert_user(1, "alice", "Alice")
        await db_mod.upsert_user(2, "bob", "Bob")   # no inbound
        await db_mod.approve_user(1)
        await db_mod.save_inbound(
            telegram_id=1, inbound_id=10, port=30001,
            client_uuid="uuid-1", sub_id="sub1",
        )
        rows = await db_mod.list_users_with_inbounds()
        assert len(rows) == 1
        assert rows[0]["telegram_id"] == 1

    @pytest.mark.asyncio
    async def test_returns_correct_fields(self, tmp_db):
        await db_mod.upsert_user(1, "alice", "Alice")
        await db_mod.approve_user(1)
        await db_mod.save_inbound(
            telegram_id=1, inbound_id=42, port=30001,
            client_uuid="uuid-x", sub_id="sub-x",
        )
        rows = await db_mod.list_users_with_inbounds()
        row = rows[0]
        assert row["inbound_id"] == 42
        assert row["port"] == 30001
        assert row["client_uuid"] == "uuid-x"

    @pytest.mark.asyncio
    async def test_multiple_users(self, tmp_db):
        for i in range(1, 4):
            await db_mod.upsert_user(i, f"user{i}", f"User{i}")
            await db_mod.approve_user(i)
            await db_mod.save_inbound(
                telegram_id=i, inbound_id=i * 10, port=30000 + i,
                client_uuid=f"uuid-{i}", sub_id=f"sub-{i}",
            )
        rows = await db_mod.list_users_with_inbounds()
        assert len(rows) == 3
        assert {r["telegram_id"] for r in rows} == {1, 2, 3}


# ── /reissue_all handler ──────────────────────────────────────────────────────

def _import_bot():
    import importlib
    if "bot" in sys.modules:
        return sys.modules["bot"]
    with patch("aiogram.Bot.get_me"):
        return importlib.import_module("bot")


def _make_message(user_id: int, text: str) -> MagicMock:
    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.text = text
    msg.answer = AsyncMock()
    return msg


class TestReissueAll:
    @pytest.mark.asyncio
    async def test_non_admin_ignored(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=999, text="/reissue_all")
        with patch("bot.is_admin", return_value=False):
            await bot_mod.cmd_reissue_all(msg)
        msg.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_users_with_inbounds(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=100, text="/reissue_all")
        with patch("bot.is_admin", return_value=True), \
             patch("bot.db.list_users_with_inbounds", new=AsyncMock(return_value=[])):
            await bot_mod.cmd_reissue_all(msg)
        msg.answer.assert_called_once()
        assert "Нет" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_sends_link_to_each_user(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=100, text="/reissue_all")

        users = [
            {"telegram_id": 1, "inbound_id": 10, "port": 30001, "client_uuid": "a", "sub_id": "s1"},
            {"telegram_id": 2, "inbound_id": 20, "port": 30002, "client_uuid": "b", "sub_id": "s2"},
        ]
        inbound_record = {"inbound_id": 10, "port": 30001, "client_uuid": "a", "sub_id": "s1"}
        deliver_mock = AsyncMock()

        with patch("bot.is_admin", return_value=True), \
             patch("bot.db.list_users_with_inbounds", new=AsyncMock(return_value=users)), \
             patch("bot.db.get_user_inbound", new=AsyncMock(return_value=inbound_record)), \
             patch("bot._deliver_link", deliver_mock), \
             patch("asyncio.sleep", new=AsyncMock()):
            await bot_mod.cmd_reissue_all(msg)

        assert deliver_mock.call_count == 2
        called_ids = [c[0][0] for c in deliver_mock.call_args_list]
        assert 1 in called_ids
        assert 2 in called_ids

    @pytest.mark.asyncio
    async def test_reports_ok_and_fail_counts(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=100, text="/reissue_all")

        users = [
            {"telegram_id": 1, "inbound_id": 10, "port": 30001, "client_uuid": "a", "sub_id": "s"},
            {"telegram_id": 2, "inbound_id": 20, "port": 30002, "client_uuid": "b", "sub_id": "s"},
        ]

        call_count = 0
        async def flaky_deliver(uid, record):
            nonlocal call_count
            call_count += 1
            if uid == 2:
                raise RuntimeError("network error")

        with patch("bot.is_admin", return_value=True), \
             patch("bot.db.list_users_with_inbounds", new=AsyncMock(return_value=users)), \
             patch("bot.db.get_user_inbound", new=AsyncMock(return_value={})), \
             patch("bot._deliver_link", side_effect=flaky_deliver), \
             patch("asyncio.sleep", new=AsyncMock()):
            await bot_mod.cmd_reissue_all(msg)

        # Last answer should mention 1 ok and 1 fail
        final = msg.answer.call_args_list[-1][0][0]
        assert "1" in final   # ok count
        assert "1" in final   # fail count

    @pytest.mark.asyncio
    async def test_sends_start_and_done_messages(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=100, text="/reissue_all")

        users = [{"telegram_id": 1, "inbound_id": 10, "port": 30001, "client_uuid": "a", "sub_id": "s"}]

        with patch("bot.is_admin", return_value=True), \
             patch("bot.db.list_users_with_inbounds", new=AsyncMock(return_value=users)), \
             patch("bot.db.get_user_inbound", new=AsyncMock(return_value={})), \
             patch("bot._deliver_link", new=AsyncMock()), \
             patch("asyncio.sleep", new=AsyncMock()):
            await bot_mod.cmd_reissue_all(msg)

        # Should send at least 2 messages: "начинаю..." and "готово..."
        assert msg.answer.call_count >= 2
        texts = [c[0][0] for c in msg.answer.call_args_list]
        assert any("Начинаю" in t for t in texts)
        assert any("Готово" in t for t in texts)
