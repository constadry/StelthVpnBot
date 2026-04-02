import asyncio
import logging
import random
import string
import uuid
from typing import Optional

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage

import db
from config import load_config
from panel_client import PanelClient, PanelError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

config = load_config()
bot = Bot(token=config.bot_token)
dp = Dispatcher(storage=MemoryStorage())

panel = PanelClient(
    base_url=config.panel_url,
    username=config.panel_user,
    password=config.panel_pass,
    verify_ssl=config.panel_verify_ssl,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_admin(user_id: int) -> bool:
    return user_id in config.admin_ids


def _user_tag(telegram_id: int) -> str:
    return f"user-{telegram_id}"


def _gen_sub_id(length: int = 16) -> str:
    """Random alphanumeric subId matching 3x-ui format."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


async def _pick_free_port() -> int:
    used = await db.get_used_ports()
    for port in range(config.port_range_start, config.port_range_end + 1):
        if port not in used:
            return port
    raise RuntimeError("No free ports available in the configured range.")


# ---------------------------------------------------------------------------
# Access guard middleware
# ---------------------------------------------------------------------------

@dp.message.middleware()
async def register_user_middleware(handler, message: types.Message, data: dict):
    """Always upsert the user so admins can /approve them later."""
    if message.from_user:
        await db.upsert_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
    return await handler(message, data)


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    approved = await db.is_approved(message.from_user.id)
    if approved:
        text = (
            "Привет! Я выдаю персональные VPN-ссылки.\n\n"
            "Команды:\n"
            "/getlink — получить VLESS-ссылку подключения\n"
            "/sub — получить ссылку-подписку (все серверы сразу)\n"
        )
    else:
        text = (
            "Привет! Твой запрос зарегистрирован.\n"
            "Дождись одобрения от администратора."
        )
    await message.answer(text)


# ---------------------------------------------------------------------------
# /getlink — create inbound if needed and return VLESS link
# ---------------------------------------------------------------------------

@dp.message(Command("getlink"))
async def cmd_getlink(message: types.Message):
    uid = message.from_user.id

    if not await db.is_approved(uid):
        await message.answer("У тебя нет доступа. Обратись к администратору.")
        return

    existing = await db.get_user_inbound(uid)
    if existing:
        await _send_vless_link(message, existing)
        return

    await message.answer("Создаю твой персональный сервер, подожди секунду...")

    try:
        port = await _pick_free_port()
        client_uuid = str(uuid.uuid4())
        sub_id = _gen_sub_id()
        email = f"tg_{uid}"
        tag = _user_tag(uid)

        inbound_data = await panel.create_inbound(
            port=port,
            tag=tag,
            client_uuid=client_uuid,
            email=email,
            sub_id=sub_id,
        )

        # 3x-ui returns the created inbound object; fall back to fetching by tag
        inbound_id: Optional[int] = None
        if isinstance(inbound_data, dict):
            inbound_id = inbound_data.get("id")

        if inbound_id is None:
            inbound_id = await _find_inbound_id_by_tag(tag)

        if inbound_id is None:
            await message.answer("Не удалось определить ID созданного сервера. Обратись к администратору.")
            return

        await db.save_inbound(
            telegram_id=uid,
            inbound_id=inbound_id,
            port=port,
            client_uuid=client_uuid,
            sub_id=sub_id,
        )

        record = await db.get_user_inbound(uid)
        await _send_vless_link(message, record)

    except PanelError as e:
        logger.error("Panel error for user %s: %s", uid, e)
        await message.answer(f"Ошибка панели: {e}")
    except Exception as e:
        logger.exception("Unexpected error for user %s", uid)
        await message.answer("Произошла непредвиденная ошибка. Попробуй позже.")


async def _find_inbound_id_by_tag(tag: str) -> Optional[int]:
    inbounds = await panel.get_inbounds()
    for ib in inbounds:
        if ib.get("tag") == tag:
            return ib.get("id")
    return None


async def _send_vless_link(message: types.Message, record: dict) -> None:
    server_ip = config.panel_url.split("//")[-1].split("/")[0].split(":")[0]

    try:
        inbound_data = await panel.get_inbound(record["inbound_id"])
    except PanelError as e:
        logger.error("Cannot fetch inbound %s: %s", record["inbound_id"], e)
        inbound_data = {}

    vless = PanelClient.build_vless_link(
        server_ip=server_ip,
        port=record["port"],
        client_uuid=record["client_uuid"],
        inbound_data=inbound_data,
        remark="MyVPN",
    )

    await message.answer(
        f"Твоя VLESS-ссылка:\n\n`{vless}`\n\n"
        "Импортируй в Happ / Hiddify / v2rayN.",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# /sub — subscription link
# ---------------------------------------------------------------------------

@dp.message(Command("sub"))
async def cmd_sub(message: types.Message):
    uid = message.from_user.id

    if not await db.is_approved(uid):
        await message.answer("У тебя нет доступа. Обратись к администратору.")
        return

    record = await db.get_user_inbound(uid)
    if not record:
        await message.answer("У тебя ещё нет сервера. Используй /getlink чтобы создать.")
        return

    sub_link = PanelClient.build_sub_link(
        panel_base_url=config.panel_url,
        sub_port=2096,
        sub_id=record["sub_id"],
    )

    await message.answer(
        f"Твоя ссылка-подписка:\n\n`{sub_link}`\n\n"
        "Импортируй в Happ / Hiddify — все серверы подтянутся автоматически.",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Admin: /approve /revoke /list
# ---------------------------------------------------------------------------

@dp.message(Command("approve"))
async def cmd_approve(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Использование: /approve <telegram_id>")
        return

    target_id = int(parts[1])
    ok = await db.approve_user(target_id)
    if ok:
        await message.answer(f"Пользователь {target_id} одобрен.")
        try:
            await bot.send_message(target_id, "Твой доступ одобрен! Используй /getlink.")
        except Exception:
            pass
    else:
        await message.answer(
            f"Пользователь {target_id} не найден в базе.\n"
            "Он должен сначала написать боту /start."
        )


@dp.message(Command("revoke"))
async def cmd_revoke(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Использование: /revoke <telegram_id>")
        return

    target_id = int(parts[1])
    ok = await db.revoke_user(target_id)
    await message.answer(
        f"Доступ пользователя {target_id} отозван." if ok
        else f"Пользователь {target_id} не найден."
    )


@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    users = await db.list_users()
    if not users:
        await message.answer("Нет пользователей в базе.")
        return

    lines = ["<b>Пользователи:</b>"]
    for u in users:
        status = "✅" if u["approved"] else "⏳"
        name = u["full_name"] or u["username"] or "—"
        port_info = f"port={u['port']}" if u["port"] else "нет сервера"
        lines.append(f"{status} <code>{u['telegram_id']}</code> {name} — {port_info}")

    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("backup"))
async def cmd_backup(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await db.backup_db()
    await message.answer("Бэкап базы выполнен.")


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

async def _periodic_backup():
    """Hourly backup to protect against container restart data loss."""
    while True:
        await asyncio.sleep(3600)
        try:
            await db.backup_db()
            logger.info("Periodic backup completed.")
        except Exception as e:
            logger.error("Periodic backup failed: %s", e)


async def main():
    db.init_db_config(config.db_path, config.db_backup_path)
    await db.init_db()

    asyncio.create_task(_periodic_backup())

    logger.info("Bot starting...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
