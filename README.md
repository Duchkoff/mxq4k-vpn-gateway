# MXQ 4K → Linux VPN-шлюз

Превращение бюджетной ТВ-приставки MXQ 4K (Rockchip RK3229, NAND) в прозрачный VPN-шлюз для локальной сети: Armbian в NAND, xray + hysteria2 (sing-box), веб-панель управления, маршрутизация по категориям (Gemini / игры / YouTube / заблокированные РКН).

## Что умеет

- **Прозрачный шлюз**: устройство в сети прописывает шлюз = IP приставки — и весь его трафик идёт по правилам VPN без настройки прокси.
- **Режимы**: «весь трафик через VPN» (кроме RU-сервисов и RU-IP) и «обход» (через VPN только заблокированное: YouTube, Instagram, Discord, Telegram, WhatsApp, SoundCloud + ~83 000 доменов реестра РКН и «ушедших» сервисов, списки автообновляются раз в час).
- **Категории с отдельным выбором сервера**: Gemini/AI (включая Antigravity), игры (Roblox, Supercell, Garena, HoYoverse), YouTube — для каждой свой сервер из пула, выбор в веб-панели с пингом.
- **Hysteria2**: xray не умеет — рядом крутится sing-box-сайдкар, панель сама парсит hysteria-серверы из подписки.
- **DoT-DNS**: провайдер душит UDP/53 → stubby (DNS-over-TLS) + dnsmasq с кэшем.
- **Веб-панель** (порт 8080): подписки Remnawave/Happ, добавление vless-ссылок, пинг серверов, вкл/выкл, кнопка обновления подписок.

## Состав

- `docs/ИНСТРУКЦИЯ.md` — полная инструкция по перепрошивке NAND (Multitool, maskrom-особенности RK3229), настройке Armbian, xray, шлюза.
- `docs/NOTES.md` — рабочие заметки: архитектура, подводные камни (FTL-заголовок NAND, `systemctl enable --now` не рестартит xray, ECH/QUIC, PPPoE роутера, API-логин Huawei AX2).
- `files/status-web.py` — веб-панель (генерация конфига xray, маршрутизация по категориям, sing-box планировщик).
- `files/sub-update.py` — автообновление подписок и списков заблокированных (systemd timer, раз в час).
- `files/gw-rules.sh` — iptables-правила прозрачного шлюза (REDIRECT в xray, блокировка QUIC, MASQUERADE).
- `files/stubby.yml`, `files/gateway.conf` — DNS-over-TLS и dnsmasq.

## Железо

MXQ 4K, плата XT-MX4N-V03, RK3229, NAND Micron 8 ГБ, Wi-Fi SSV6051P (драйвер мёртв — не используется), кабель 100 Мбит.

Все пароли, токены подписок и HWID в документах заменены на плейсхолдеры.
