"""
Unit tests for the moderator feature.
Run: pytest tests/test_moderators.py -v
"""
import os
import sys

# Must be set before bot/config import so load_dotenv doesn't override them
os.environ.update({
    "BOT_TOKEN": "0:AAtest",
    "ADMIN_IDS": "100",
    "PANEL_URL": "http://127.0.0.1:9",
    "PANEL_USER": "u",
    "PANEL_PASS": "p",
})

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import db as db_mod

# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def tmp_db(tmp_path):
    path = str(tmp_path / "bot.db")
    db_mod.init_db_config(path, path + ".bak")
    await db_mod.init_db()
    yield


# ── DB: moderator CRUD ───────────────────────────────────────────────────────

class TestModeratorDB:
    @pytest.mark.asyncio
    async def test_add_and_is_moderator(self, tmp_db):
        await db_mod.add_moderator(111, "alice", added_by=100)
        assert await db_mod.is_moderator(111) is True

    @pytest.mark.asyncio
    async def test_unknown_is_not_moderator(self, tmp_db):
        assert await db_mod.is_moderator(999) is False

    @pytest.mark.asyncio
    async def test_remove_existing(self, tmp_db):
        await db_mod.add_moderator(111, "alice", added_by=100)
        assert await db_mod.remove_moderator(111) is True
        assert await db_mod.is_moderator(111) is False

    @pytest.mark.asyncio
    async def test_remove_nonexistent_returns_false(self, tmp_db):
        assert await db_mod.remove_moderator(999) is False

    @pytest.mark.asyncio
    async def test_list_moderators(self, tmp_db):
        await db_mod.add_moderator(111, "alice", added_by=100)
        await db_mod.add_moderator(222, "bob", added_by=100)
        mods = await db_mod.list_moderators()
        assert {m["telegram_id"] for m in mods} == {111, 222}

    @pytest.mark.asyncio
    async def test_list_empty(self, tmp_db):
        assert await db_mod.list_moderators() == []

    @pytest.mark.asyncio
    async def test_add_duplicate_replaces(self, tmp_db):
        await db_mod.add_moderator(111, "alice", added_by=100)
        await db_mod.add_moderator(111, "alice_v2", added_by=100)
        mods = await db_mod.list_moderators()
        assert len(mods) == 1
        assert mods[0]["username"] == "alice_v2"

    @pytest.mark.asyncio
    async def test_get_user_by_username_found(self, tmp_db):
        await db_mod.upsert_user(555, "charlie", "Charlie C")
        row = await db_mod.get_user_by_username("charlie")
        assert row is not None
        assert row["telegram_id"] == 555

    @pytest.mark.asyncio
    async def test_get_user_by_username_not_found(self, tmp_db):
        assert await db_mod.get_user_by_username("nobody") is None

    @pytest.mark.asyncio
    async def test_get_user_by_username_without_at(self, tmp_db):
        """Lookup must be by bare username, not @username."""
        await db_mod.upsert_user(555, "charlie", "Charlie C")
        assert await db_mod.get_user_by_username("@charlie") is None
        assert await db_mod.get_user_by_username("charlie") is not None


# ── is_staff ──────────────────────────────────────────────────────────────────

# Import bot lazily inside tests to ensure env vars are already set.
# We patch bot.bot (the Bot instance) to avoid real Telegram connections.

def _import_bot():
    import importlib
    if "bot" in sys.modules:
        return sys.modules["bot"]
    with patch("aiogram.Bot.get_me"):
        return importlib.import_module("bot")


class TestIsStaff:
    @pytest.mark.asyncio
    async def test_admin_is_staff(self):
        bot_mod = _import_bot()
        with patch("bot.is_admin", return_value=True):
            assert await bot_mod.is_staff(100) is True

    @pytest.mark.asyncio
    async def test_moderator_is_staff(self):
        bot_mod = _import_bot()
        with patch("bot.is_admin", return_value=False), \
             patch("bot.db.is_moderator", new=AsyncMock(return_value=True)):
            assert await bot_mod.is_staff(111) is True

    @pytest.mark.asyncio
    async def test_regular_user_not_staff(self):
        bot_mod = _import_bot()
        with patch("bot.is_admin", return_value=False), \
             patch("bot.db.is_moderator", new=AsyncMock(return_value=False)):
            assert await bot_mod.is_staff(999) is False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_message(user_id: int, text: str) -> MagicMock:
    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.from_user.username = "tester"
    msg.from_user.full_name = "Tester"
    msg.text = text
    msg.answer = AsyncMock()
    return msg


def _make_callback(user_id: int, data: str) -> MagicMock:
    cb = MagicMock()
    cb.from_user = MagicMock()
    cb.from_user.id = user_id
    cb.data = data
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.answer = AsyncMock()
    cb.message.edit_reply_markup = AsyncMock()
    return cb


# ── /addmod handler ───────────────────────────────────────────────────────────

class TestCmdAddmod:
    @pytest.mark.asyncio
    async def test_non_admin_is_ignored(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=999, text="/addmod 111")
        with patch("bot.is_admin", return_value=False):
            await bot_mod.cmd_addmod(msg)
        msg.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_arg_shows_usage(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=100, text="/addmod")
        with patch("bot.is_admin", return_value=True):
            await bot_mod.cmd_addmod(msg)
        msg.answer.assert_called_once()
        assert "Использование" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_username_not_found(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=100, text="/addmod @ghost")
        with patch("bot.is_admin", return_value=True), \
             patch("bot.db.get_user_by_username", new=AsyncMock(return_value=None)):
            await bot_mod.cmd_addmod(msg)
        msg.answer.assert_called_once()
        assert "не найден" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_add_by_username_success(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=100, text="/addmod @alice")
        with patch("bot.is_admin", side_effect=lambda uid: uid == 100), \
             patch("bot.db.get_user_by_username", new=AsyncMock(return_value={"telegram_id": 111, "username": "alice"})), \
             patch("bot.db.add_moderator", new=AsyncMock()) as mock_add, \
             patch("bot.bot.send_message", new=AsyncMock()):
            await bot_mod.cmd_addmod(msg)
        mock_add.assert_called_once_with(111, "alice", 100)
        msg.answer.assert_called_once()
        assert "добавлен" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_add_by_numeric_id_success(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=100, text="/addmod 222")
        with patch("bot.is_admin", side_effect=lambda uid: uid == 100), \
             patch("bot.db.add_moderator", new=AsyncMock()) as mock_add, \
             patch("bot.bot.send_message", new=AsyncMock()):
            await bot_mod.cmd_addmod(msg)
        mock_add.assert_called_once_with(222, None, 100)

    @pytest.mark.asyncio
    async def test_cannot_add_existing_admin_as_mod(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=100, text="/addmod 100")
        with patch("bot.is_admin", return_value=True), \
             patch("bot.db.add_moderator", new=AsyncMock()) as mock_add:
            await bot_mod.cmd_addmod(msg)
        mock_add.assert_not_called()
        assert "уже является администратором" in msg.answer.call_args[0][0]


# ── /removemod handler ────────────────────────────────────────────────────────

class TestCmdRemovemod:
    @pytest.mark.asyncio
    async def test_non_admin_is_ignored(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=999, text="/removemod 111")
        with patch("bot.is_admin", return_value=False):
            await bot_mod.cmd_removemod(msg)
        msg.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_arg_shows_usage(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=100, text="/removemod")
        with patch("bot.is_admin", return_value=True):
            await bot_mod.cmd_removemod(msg)
        assert "Использование" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_remove_existing(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=100, text="/removemod 111")
        with patch("bot.is_admin", return_value=True), \
             patch("bot.db.remove_moderator", new=AsyncMock(return_value=True)), \
             patch("bot.bot.send_message", new=AsyncMock()):
            await bot_mod.cmd_removemod(msg)
        assert "удалён" in msg.answer.call_args[0][0]

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=100, text="/removemod 999")
        with patch("bot.is_admin", return_value=True), \
             patch("bot.db.remove_moderator", new=AsyncMock(return_value=False)):
            await bot_mod.cmd_removemod(msg)
        assert "не найден" in msg.answer.call_args[0][0]


# ── /mods handler ─────────────────────────────────────────────────────────────

class TestCmdMods:
    @pytest.mark.asyncio
    async def test_non_admin_is_ignored(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=999, text="/mods")
        with patch("bot.is_admin", return_value=False):
            await bot_mod.cmd_mods(msg)
        msg.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_list(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=100, text="/mods")
        with patch("bot.is_admin", return_value=True), \
             patch("bot.db.list_moderators", new=AsyncMock(return_value=[])):
            await bot_mod.cmd_mods(msg)
        assert "нет" in msg.answer.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_shows_moderators(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=100, text="/mods")
        mods = [
            {"telegram_id": 111, "username": "alice", "added_by": 100},
            {"telegram_id": 222, "username": None, "added_by": 100},
        ]
        with patch("bot.is_admin", return_value=True), \
             patch("bot.db.list_moderators", new=AsyncMock(return_value=mods)):
            await bot_mod.cmd_mods(msg)
        text = msg.answer.call_args[0][0]
        assert "111" in text
        assert "222" in text


# ── cb_issue_link access check ────────────────────────────────────────────────

class TestCbIssueLinkAccess:
    @pytest.mark.asyncio
    async def test_non_staff_denied(self):
        bot_mod = _import_bot()
        cb = _make_callback(user_id=999, data="issue:555")
        with patch("bot.is_staff", new=AsyncMock(return_value=False)):
            await bot_mod.cb_issue_link(cb)
        cb.answer.assert_called_once()
        assert cb.answer.call_args[1].get("show_alert") is True

    @pytest.mark.asyncio
    async def test_moderator_passes_access_check(self):
        bot_mod = _import_bot()
        cb = _make_callback(user_id=111, data="issue:555")
        with patch("bot.is_staff", new=AsyncMock(return_value=True)), \
             patch("bot.db.count_issued_by_admin", new=AsyncMock(return_value=25)):
            await bot_mod.cb_issue_link(cb)
        # Denied by limit, not by access — answer called with show_alert for limit msg
        text = cb.answer.call_args[0][0] if cb.answer.call_args[0] else cb.answer.call_args[1].get("text", "")
        assert "лимит" in text.lower() or "Достигнут" in text

    @pytest.mark.asyncio
    async def test_staff_within_limit_proceeds(self):
        bot_mod = _import_bot()
        cb = _make_callback(user_id=111, data="issue:555")
        with patch("bot.is_staff", new=AsyncMock(return_value=True)), \
             patch("bot.db.count_issued_by_admin", new=AsyncMock(return_value=0)), \
             patch("bot.db.get_user_inbound", new=AsyncMock(return_value=None)), \
             patch("bot._pick_free_port", new=AsyncMock(return_value=30001)), \
             patch("bot.panel.create_inbound", new=AsyncMock(return_value={"id": 42})), \
             patch("bot.db.approve_user", new=AsyncMock()), \
             patch("bot.db.save_inbound", new=AsyncMock()), \
             patch("bot.db.get_user_inbound", new=AsyncMock(return_value={
                 "inbound_id": 42, "port": 30001, "client_uuid": "u", "sub_id": "s"
             })), \
             patch("bot._deliver_link", new=AsyncMock()):
            await bot_mod.cb_issue_link(cb)
        # First answer call should NOT be the "Нет доступа" alert
        first_call_kwargs = cb.answer.call_args_list[0][1] if cb.answer.call_args_list else {}
        assert first_call_kwargs.get("show_alert") is not True


# ── /start notifies moderators ────────────────────────────────────────────────

class TestStartNotifiesModerators:
    @pytest.mark.asyncio
    async def test_moderators_receive_notification(self):
        bot_mod = _import_bot()
        msg = _make_message(user_id=555, text="/start")

        mods = [{"telegram_id": 111}, {"telegram_id": 222}]
        send_mock = AsyncMock()

        with patch("bot.db.is_approved", new=AsyncMock(return_value=False)), \
             patch("bot.db.list_moderators", new=AsyncMock(return_value=mods)), \
             patch("bot.bot.send_message", new=send_mock):
            await bot_mod.cmd_start(msg)

        notified = {call[0][0] for call in send_mock.call_args_list}
        # admin 100 + moderators 111 and 222 must all be notified
        assert 100 in notified
        assert 111 in notified
        assert 222 in notified

    @pytest.mark.asyncio
    async def test_no_duplicate_notifications(self):
        """A moderator who is also in ADMIN_IDS must get only one message."""
        bot_mod = _import_bot()
        msg = _make_message(user_id=555, text="/start")

        # Moderator with same ID as admin
        mods = [{"telegram_id": 100}]
        send_mock = AsyncMock()

        with patch("bot.db.is_approved", new=AsyncMock(return_value=False)), \
             patch("bot.db.list_moderators", new=AsyncMock(return_value=mods)), \
             patch("bot.bot.send_message", new=send_mock):
            await bot_mod.cmd_start(msg)

        notified = [call[0][0] for call in send_mock.call_args_list]
        assert notified.count(100) == 1
