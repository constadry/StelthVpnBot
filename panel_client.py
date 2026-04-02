import copy
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import aiohttp

logger = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).parent / "inbound_template.json"


class PanelError(Exception):
    pass


class PanelClient:
    """Async client for the 3x-ui REST API.

    A new aiohttp.ClientSession is created per logical operation so the bot
    never holds stale cookies or dangling TCP connections between requests.
    """

    def __init__(self, base_url: str, username: str, password: str, verify_ssl: bool = False):
        self._base_url = base_url
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session(self) -> aiohttp.ClientSession:
        connector = aiohttp.TCPConnector(ssl=self._verify_ssl)
        return aiohttp.ClientSession(connector=connector)

    async def _login(self, session: aiohttp.ClientSession) -> None:
        resp = await session.post(
            f"{self._base_url}/login",
            json={"username": self._username, "password": self._password},
        )
        data = await resp.json()
        if not data.get("success"):
            raise PanelError(f"Login failed: {data.get('msg', 'unknown error')}")

    async def _get(self, path: str) -> Any:
        async with self._session() as session:
            await self._login(session)
            resp = await session.get(f"{self._base_url}{path}")
            data = await resp.json()
            if not data.get("success"):
                raise PanelError(f"GET {path} failed: {data.get('msg')}")
            return data.get("obj")

    async def _post(self, path: str, payload: Any) -> Any:
        async with self._session() as session:
            await self._login(session)
            resp = await session.post(
                f"{self._base_url}{path}",
                json=payload,
            )
            data = await resp.json()
            if not data.get("success"):
                raise PanelError(f"POST {path} failed: {data.get('msg')}")
            return data.get("obj")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_inbounds(self) -> list[dict]:
        return await self._get("/xui/inbound/list") or []

    async def get_inbound(self, inbound_id: int) -> dict:
        result = await self._get(f"/xui/inbound/get/{inbound_id}")
        return result

    async def create_inbound(self, port: int, tag: str, client_uuid: str, email: str) -> dict:
        """Create a new inbound based on inbound_template.json.

        Substitutes placeholders:
            {{PORT}}         → port (int)
            {{TAG}}          → unique tag string
            {{CLIENT_UUID}}  → UUID for the VLESS client
            {{EMAIL}}        → client email / remark
        """
        if not TEMPLATE_PATH.exists():
            raise PanelError(
                "inbound_template.json not found. "
                "Please create it based on your existing inbound."
            )

        raw = TEMPLATE_PATH.read_text(encoding="utf-8")
        raw = (
            raw
            .replace("{{PORT}}", str(port))
            .replace("{{TAG}}", tag)
            .replace("{{CLIENT_UUID}}", client_uuid)
            .replace("{{EMAIL}}", email)
        )

        payload = json.loads(raw)
        result = await self._post("/xui/inbound/add", payload)
        return result

    async def delete_inbound(self, inbound_id: int) -> None:
        await self._post(f"/xui/inbound/del/{inbound_id}", {})

    # ------------------------------------------------------------------
    # VLESS link builder
    # ------------------------------------------------------------------

    @staticmethod
    def build_vless_link(
        server_ip: str,
        port: int,
        client_uuid: str,
        inbound_data: dict,
        remark: str = "VPN",
    ) -> str:
        """Build a vless:// URI from inbound streamSettings.

        Works for Reality over TCP (the main inbound type used here).
        Falls back gracefully if some fields are missing.
        """
        stream: dict = {}
        try:
            stream = json.loads(inbound_data.get("streamSettings") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass

        network = stream.get("network", "tcp")
        security = stream.get("security", "reality")

        reality = stream.get("realitySettings") or {}
        public_key = reality.get("publicKey", "")
        short_id = (reality.get("shortIds") or [""])[0]
        sni = (reality.get("serverNames") or [""])[0]

        tls = stream.get("tlsSettings") or {}
        fingerprint = (tls.get("fingerprint") or reality.get("fingerprint") or "chrome")

        params = {
            "type": network,
            "security": security,
            "flow": "xtls-rprx-vision",
        }

        if security == "reality":
            params["pbk"] = public_key
            params["fp"] = fingerprint
            params["sni"] = sni
            params["sid"] = short_id

        query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items() if v)
        encoded_remark = quote(remark)
        return f"vless://{client_uuid}@{server_ip}:{port}?{query}#{encoded_remark}"

    @staticmethod
    def build_sub_link(panel_base_url: str, sub_port: int, client_uuid: str) -> str:
        """Subscription link understood by Happ/Hiddify/v2rayN."""
        host = panel_base_url.split("//")[-1].split("/")[0].split(":")[0]
        return f"http://{host}:{sub_port}/sub/{client_uuid}"
