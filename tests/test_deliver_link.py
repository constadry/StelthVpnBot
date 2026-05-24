"""Unit test for _deliver_link: VLESS link must be wrapped in <u> tags."""
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
from unittest.mock import AsyncMock, MagicMock, patch


def _import_bot():
    import importlib
    return sys.modules.get("bot") or importlib.import_module("bot")


@pytest.mark.asyncio
async def test_deliver_link_uses_html_underline():
    bot_mod = _import_bot()

    fake_inbound = {
        "port": 30001,
        "protocol": "vless",
        "streamSettings": "{}",
        "settings": '{"clients": [{"id": "uuid-test", "flow": ""}]}',
    }
    record = {
        "inbound_id": 1,
        "port": 30001,
        "client_uuid": "uuid-test",
        "sub_id": "abc",
    }

    send_mock = AsyncMock()

    with patch("bot.panel.get_inbound", new=AsyncMock(return_value=fake_inbound)), \
         patch("bot.bot.send_message", new=send_mock):
        await bot_mod._deliver_link(555, record)

    send_mock.assert_called_once()
    call_kwargs = send_mock.call_args[1]
    call_text = send_mock.call_args[0][1] if send_mock.call_args[0] else call_kwargs.get("text", "")

    assert call_kwargs.get("parse_mode") == "HTML", "parse_mode должен быть HTML"
    assert "<u>" in call_text and "</u>" in call_text, "ссылка должна быть обёрнута в <u>...</u>"
    assert "`" not in call_text, "не должно быть Markdown-бэктиков"
