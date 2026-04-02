import asyncio
import json
import logging
import random
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import aiohttp

logger = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).parent / "inbound_template.json"
WHITELIST_URL = "https://raw.githubusercontent.com/hxehex/russia-mobile-internet-whitelist/main/whitelist.txt"
WHITELIST_TTL = 3600  # seconds


class WhitelistCache:
    """Fetches and caches the SNI whitelist from GitHub.

    Refreshes automatically after TTL expires so the bot always has
    a fresh list without blocking on every inbound creation.
    """

    def __init__(self, url: str = WHITELIST_URL, ttl: int = WHITELIST_TTL):
        self._url = url
        self._ttl = ttl
        self._domains: list[str] = []
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    def _is_stale(self) -> bool:
        return time.monotonic() - self._fetched_at > self._ttl

    @staticmethod
    def _parse(text: str) -> list[str]:
        domains = []
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "." in line:
                domains.append(line)
        return domains

    async def get_domains(self) -> list[str]:
        if self._domains and not self._is_stale():
            return self._domains

        async with self._lock:
            # Double-check after acquiring lock
            if self._domains and not self._is_stale():
                return self._domains

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(self._url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        resp.raise_for_status()
                        text = await resp.text()
                domains = self._parse(text)
                if domains:
                    self._domains = domains
                    self._fetched_at = time.monotonic()
                    logger.info("Whitelist updated: %d domains loaded", len(domains))
                else:
                    logger.warning("Whitelist is empty, keeping previous list")
            except Exception as e:
                logger.error("Failed to fetch whitelist: %s", e)
                if not self._domains:
                    # Hard fallback if we've never loaded successfully
                    self._domains = ["sun9-77.userapi.com"]

        return self._domains

    async def random_domain(self) -> str:
        domains = await self.get_domains()
        return random.choice(domains)


# Module-level singleton — shared across all panel operations
whitelist = WhitelistCache()


class PanelError(Exception):
    pass


class PanelClient:
    """Async client for the 3x-ui REST API.

    A new aiohttp.ClientSession is created per logical operation so the bot
    never holds stale cookies or dangling TCP connections between requests.
    """

    def __init__(self, base_url: str, username: str, password: str, verify_ssl: bool = False, api_prefix: str = "/panel"):
        self._base_url = base_url
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._api_prefix = api_prefix.rstrip("/")

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
        raw = await resp.text()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("Login — non-JSON response (HTTP %s):\n%s", resp.status, raw[:500])
            raise PanelError(
                f"Login returned non-JSON (HTTP {resp.status}). "
                "Check PANEL_URL and panel availability."
            )
        if not data.get("success"):
            raise PanelError(f"Login failed: {data.get('msg', 'unknown error')}")

    async def _parse_response(self, resp: aiohttp.ClientResponse, label: str) -> Any:
        raw = await resp.text()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.error("%s — non-JSON response (HTTP %s):\n%s", label, resp.status, raw[:500])
            raise PanelError(
                f"{label} returned non-JSON (HTTP {resp.status}). "
                "Check PANEL_URL in .env and panel logs."
            )
        if not data.get("success"):
            logger.error("%s failed: %s", label, data.get("msg"))
            raise PanelError(f"{label} failed: {data.get('msg')}")
        return data.get("obj")

    async def _get(self, path: str) -> Any:
        async with self._session() as session:
            await self._login(session)
            resp = await session.get(f"{self._base_url}{self._api_prefix}{path}")
            return await self._parse_response(resp, f"GET {path}")

    async def _post(self, path: str, payload: Any) -> Any:
        async with self._session() as session:
            await self._login(session)
            resp = await session.post(
                f"{self._base_url}{self._api_prefix}{path}",
                json=payload,
            )
            return await self._parse_response(resp, f"POST {path}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_inbounds(self) -> list[dict]:
        return await self._get("/xui/inbound/list") or []

    async def get_inbound(self, inbound_id: int) -> dict:
        result = await self._get(f"/xui/inbound/get/{inbound_id}")
        return result

    async def create_inbound(
        self,
        port: int,
        tag: str,
        client_uuid: str,
        email: str,
        sub_id: str = "",
        sni: Optional[str] = None,
    ) -> dict:
        """Create a new inbound based on inbound_template.json.

        Substitutes placeholders:
            {{PORT}}         → port (int)
            {{TAG}}          → unique tag string
            {{CLIENT_UUID}}  → UUID for the VLESS client
            {{EMAIL}}        → client email / remark
            {{SUB_ID}}       → subscription ID for /sub/ link
            {{SNI}}          → SNI domain (random from whitelist if not provided)
            {{TARGET}}       → SNI domain with :443 suffix (for Reality dest)
        """
        if not TEMPLATE_PATH.exists():
            raise PanelError(
                "inbound_template.json not found. "
                "Please create it based on your existing inbound."
            )

        if sni is None:
            sni = await whitelist.random_domain()

        target = f"{sni}:443"
        logger.info("Creating inbound port=%d tag=%s sni=%s", port, tag, sni)

        raw = TEMPLATE_PATH.read_text(encoding="utf-8")
        raw = (
            raw
            .replace("{{PORT}}", str(port))
            .replace("{{TAG}}", tag)
            .replace("{{CLIENT_UUID}}", client_uuid)
            .replace("{{EMAIL}}", email)
            .replace("{{SUB_ID}}", sub_id)
            .replace("{{SNI}}", sni)
            .replace("{{TARGET}}", target)
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

        Supports the 3x-ui layout where publicKey/fingerprint are nested
        inside realitySettings.settings (newer panel versions).
        """
        stream: dict = {}
        try:
            stream = json.loads(inbound_data.get("streamSettings") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass

        network = stream.get("network", "tcp")
        security = stream.get("security", "reality")

        reality = stream.get("realitySettings") or {}
        # Newer 3x-ui: publicKey and fingerprint are inside reality.settings
        reality_inner = reality.get("settings") or {}

        public_key = reality_inner.get("publicKey") or reality.get("publicKey", "")
        fingerprint = reality_inner.get("fingerprint") or reality.get("fingerprint", "chrome")
        short_id = (reality.get("shortIds") or [""])[0]
        sni = (reality.get("serverNames") or [""])[0]

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
    def build_sub_link(panel_base_url: str, sub_port: int, sub_id: str) -> str:
        """Subscription link understood by Happ/Hiddify/v2rayN.

        Uses the client's subId (not UUID) as 3x-ui expects.
        """
        host = panel_base_url.split("//")[-1].split("/")[0].split(":")[0]
        return f"http://{host}:{sub_port}/sub/{sub_id}"
