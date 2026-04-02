import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required env variable '{name}' is not set")
    return value


@dataclass
class Config:
    bot_token: str
    admin_ids: list[int]

    panel_url: str
    panel_user: str
    panel_pass: str
    panel_verify_ssl: bool

    api_prefix: str  # "/panel" for newer 3x-ui, "/xui" for older

    port_range_start: int
    port_range_end: int

    db_path: str
    db_backup_path: str


def load_config() -> Config:
    admin_raw = _require("ADMIN_IDS")
    admin_ids = [int(x.strip()) for x in admin_raw.split(",")]

    return Config(
        bot_token=_require("BOT_TOKEN"),
        admin_ids=admin_ids,
        panel_url=_require("PANEL_URL").rstrip("/"),
        panel_user=_require("PANEL_USER"),
        panel_pass=_require("PANEL_PASS"),
        panel_verify_ssl=os.getenv("PANEL_VERIFY_SSL", "false").lower() == "true",
        api_prefix=os.getenv("API_PREFIX", "/panel"),
        port_range_start=int(os.getenv("PORT_RANGE_START", "30000")),
        port_range_end=int(os.getenv("PORT_RANGE_END", "40000")),
        db_path=os.getenv("DB_PATH", "/app/data/bot.db"),
        db_backup_path=os.getenv("DB_BACKUP_PATH", "/app/data/bot.db.bak"),
    )
