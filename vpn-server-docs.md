# VPN Сервер: Документация и гайд по боту

## Стек

- **VPS** с Ubuntu
- **Docker** — контейнеризация
- **3x-ui** (ghcr.io/mhsanaei/3x-ui:latest) — панель управления Xray
- **Xray** — прокси-ядро
- **Протокол** — VLESS + XTLS-Reality
- **WARP** (Cloudflare WireGuard) — исходящий outbound для смены IP

---

## Структура сервера

```
Клиент (Happ/v2rayN)
    ↓ VLESS + Reality
VPS (72.56.109.77)
    ↓ WARP (WireGuard → Cloudflare)
Интернет
```

---

## Docker

### Запуск контейнера

```bash
docker run -d \
  --name 3x-ui \
  --restart=always \
  --network=host \
  -v /opt/3x-ui/db:/etc/x-ui \
  -v /opt/3x-ui/cert:/root/cert \
  ghcr.io/mhsanaei/3x-ui:latest
```

### Полезные команды

```bash
docker ps -a                        # статус контейнеров
docker restart 3x-ui                # перезапуск
docker logs 3x-ui --tail 50        # логи
docker start 3x-ui                  # запуск
docker stop 3x-ui                   # остановка
```

---

## Важные пути

| Путь | Описание |
|------|----------|
| `/opt/3x-ui/db` | База данных 3x-ui (конфиги, пользователи, inbound'ы) |
| `/opt/3x-ui/cert` | TLS сертификаты |
| `/etc/resolv.conf` | DNS (закреплён через `chattr +i`) |

---

## DNS (частая проблема после перезагрузки)

После перезагрузки `/etc/resolv.conf` может пропасть (это симлинк на systemd-resolved которого нет).

### Решение

```bash
chattr -i /etc/resolv.conf 2>/dev/null || true
rm -f /etc/resolv.conf
echo "nameserver 8.8.8.8
nameserver 1.1.1.1" > /etc/resolv.conf
chattr +i /etc/resolv.conf   # закрепить чтобы не пропадал
```

---

## Панель 3x-ui

- **URL**: `https://<IP>:<PORT>/<SECRET_PATH>/`
- **Порт панели**: настраивается в Panel Settings (в инструкции — 54321)
- **По умолчанию**: `http://<IP>:2053/`

---

## Inbound'ы

### Inbound 1 — VLESS + Reality (основной)

| Параметр | Значение |
|----------|----------|
| Port | 28761 |
| Tag | `inbound-28761` |
| Protocol | VLESS |
| Security | Reality |
| Flow | xtls-rprx-vision |
| uTLS | Chrome |
| Dest/SNI | домен из whitelist.txt (например из hxehex/russia-mobile-internet-whitelist) |

### Inbound 2 — VLESS + gRPC (через CDN CloudFlare, опционально)

| Параметр | Значение |
|----------|----------|
| Port | 2053 |
| Transmission | gRPC |
| Service name | my-gRPC-XXXXXXX |
| Security | TLS (сертификат из панели) |

---

## Xray Routing Config

```json
{
  "routing": {
    "domainStrategy": "AsIs",
    "rules": [
      {
        "type": "field",
        "inboundTag": ["api"],
        "outboundTag": "api"
      },
      {
        "type": "field",
        "inboundTag": ["inbound-28761"],
        "outboundTag": "direct"
      },
      {
        "type": "field",
        "outboundTag": "blocked",
        "ip": ["geoip:private"]
      },
      {
        "type": "field",
        "outboundTag": "blocked",
        "protocol": ["bittorrent"]
      },
      {
        "type": "field",
        "outboundTag": "IPv4",
        "domain": ["geosite:google"]
      },
      {
        "type": "field",
        "domain": [
          "geosite:category-ru",
          "regexp:.*\\.ru$",
          "geosite:openai"
        ],
        "outboundTag": "warp"
      },
      {
        "type": "field",
        "ip": ["geoip:ru"],
        "outboundTag": "warp"
      }
    ]
  }
}
```

### Outbound'ы

| Tag | Протокол | Описание |
|-----|----------|----------|
| `direct` | freedom | Напрямую с IP сервера |
| `blocked` | blackhole | Блокировка (торренты, private IP) |
| `IPv4` | freedom (UseIPv4) | Google через IPv4 |
| `warp` | WireGuard | Через Cloudflare WARP |

---

## Subscription Link (ссылка-подписка)

Ссылка вида:
```
http://<IP>:<PORT>/sub/<USER_TOKEN>
```

Пример:
```
http://constadry.pw:2096/sub/jm283dv64na7r6ci
```

Через эту ссылку клиенты (Happ, Hiddify, v2rayN) получают все inbound'ы автоматически.

---

## Перенос на новый сервер

```bash
# На старом сервере — скопировать базу
scp -r /opt/3x-ui/db root@<новый_ip>:/opt/3x-ui/db

# На новом сервере — перезапустить контейнер
docker restart 3x-ui
```

После переноса в клиенте нужно обновить только IP сервера — все ключи и настройки сохранятся.

---

## Скрипт установки

Скрипт `setup-vpn.sh` поддерживает два режима:
1. **Чистая установка** — DNS, Docker, 3x-ui с нуля
2. **Перенос** — копирует базу данных со старого сервера через scp

---

## Разработка бота для выдачи ссылок

### Что нужно боту

1. **Получить ссылку подключения** для пользователя из 3x-ui API
2. **Отправить ссылку** пользователю в Telegram

### 3x-ui API

3x-ui имеет REST API. Базовый URL:
```
https://<IP>:<PORT>/<SECRET_PATH>/
```

#### Авторизация

```http
POST /login
Content-Type: application/json

{"username": "admin", "password": "yourpassword"}
```

Возвращает cookie сессии.

#### Получить список inbound'ов

```http
GET /xui/inbound/list
Cookie: <session>
```

#### Получить клиентов inbound'а

```http
GET /xui/inbound/get/<inbound_id>
Cookie: <session>
```

#### Добавить клиента

```http
POST /xui/inbound/addClient
Cookie: <session>
Content-Type: application/json

{
  "id": <inbound_id>,
  "settings": "{\"clients\": [{\"id\": \"<uuid>\", \"email\": \"username\", \"enable\": true}]}"
}
```

### Subscription link для клиента

```
http://<IP>:<PORT>/sub/<client_uuid>
```

или напрямую VLESS ссылка:

```
vless://<uuid>@<IP>:<PORT>?type=tcp&security=reality&pbk=<public_key>&fp=chrome&sni=<sni>&sid=<short_id>&flow=xtls-rprx-vision#<name>
```

### Пример бота (Python + aiogram)

```python
import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

BOT_TOKEN = "YOUR_BOT_TOKEN"          # из .env
PANEL_URL = "https://constadry.pw:54321/mysecretpath"
PANEL_USER = "admin"
PANEL_PASS = "yourpassword"
INBOUND_ID = 1                        # ID вашего inbound в панели

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

async def get_panel_session():
    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            f"{PANEL_URL}/login",
            json={"username": PANEL_USER, "password": PANEL_PASS},
            ssl=False
        )
        cookies = session.cookie_jar
        return cookies

async def get_client_link(email: str):
    """Получить ссылку подключения для пользователя"""
    cookies = await get_panel_session()
    async with aiohttp.ClientSession(cookie_jar=cookies) as session:
        resp = await session.get(
            f"{PANEL_URL}/xui/inbound/get/{INBOUND_ID}",
            ssl=False
        )
        data = await resp.json()
        # Найти клиента по email и вернуть его subscription link
        # Логика зависит от структуры ответа панели
        return data

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Я выдаю ссылки для подключения к VPN.\n"
        "Команды:\n"
        "/getlink — получить ссылку подключения\n"
        "/sub — получить ссылку-подписку (все серверы сразу)"
    )

@dp.message(Command("sub"))
async def cmd_sub(message: types.Message):
    user_id = message.from_user.id
    # Здесь можно хранить маппинг user_id → client_uuid в БД
    sub_link = f"http://constadry.pw:2096/sub/jm283dv64na7r6ci"
    await message.answer(
        f"Твоя ссылка-подписка:\n`{sub_link}`\n\n"
        "Импортируй её в Happ/Hiddify — все серверы подтянутся автоматически.",
        parse_mode="Markdown"
    )

@dp.message(Command("getlink"))
async def cmd_getlink(message: types.Message):
    # Пример статической ссылки — замени на динамическую из API
    vless_link = (
        "vless://511bcefr@constadry.pw:28761"
        "?type=tcp&security=reality"
        "&pbk=YOUR_PUBLIC_KEY"
        "&fp=chrome&sni=YOUR_SNI"
        "&flow=xtls-rprx-vision"
        "#My VPN"
    )
    await message.answer(
        f"Твоя ссылка подключения:\n`{vless_link}`",
        parse_mode="Markdown"
    )

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
```

### .env файл бота

```env
BOT_TOKEN=your_telegram_bot_token
PANEL_URL=https://constadry.pw:54321/mysecretpath
PANEL_USER=admin
PANEL_PASS=yourpassword
INBOUND_ID=1
```

### Запуск бота через Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "bot.py"]
```

```yaml
# docker-compose.yml
services:
  vpn-bot:
    build: .
    restart: always
    env_file: .env
```

```bash
docker-compose up -d
```

### После смены токена в .env

```bash
docker-compose down && docker-compose up -d
# или
docker restart <имя_контейнера_бота>
```

---

## Клиенты

| Платформа | Приложение |
|-----------|-----------|
| iOS | Happ, Streisand |
| Android | Hiddify, v2rayNG |
| Windows | Hiddify-Next, v2rayN |
| macOS | Hiddify-Next, FoxRay |

### Режим Rule-based в Hiddify

Чтобы российские сайты шли напрямую (без VPN), а заблокированные через VPN:
Settings → Connection Mode → **Rule-based**

---

## Whitelist для мобильного интернета

Репозиторий: `https://github.com/hxehex/russia-mobile-internet-whitelist`

Файлы:
- `whitelist.txt` — домены (SNI) для поля Dest в Reality
- `ipwhitelist.txt` — IP адреса
- `cidrwhitelist.txt` — подсети CIDR

Использование: поставить домен из `whitelist.txt` в поле **SNI/Dest** inbound'а в 3x-ui — трафик будет работать даже при ограничениях мобильного интернета.
