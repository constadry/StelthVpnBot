# VPN Bot — инструкции для Claude

## После каждой задачи

После завершения любой задачи (новая фича, фикс, рефактор) **обязательно** вызови `/document` чтобы обновить документацию в `DOCS.md`.

Не коммить изменения без обновления документации.

## Стек

- Python 3.11, aiogram 3.x, aiosqlite, aiohttp
- 3x-ui панель (VLESS + Reality)
- Docker на сервере: `docker-compose restart vpn-bot`
- Деплой: `git push origin main` → GitHub Actions → SSH → `git fetch && git reset --hard origin/main && docker-compose restart vpn-bot`

## Сервер

- Путь: `/root/StelthVpnBot`
- `docker-compose` через дефис (старая версия)
- WSL запускать через `wsl -d Ubuntu -e bash -c "..."`

## Тесты

Перед каждым коммитом запускать: `pytest tests/ -q`
