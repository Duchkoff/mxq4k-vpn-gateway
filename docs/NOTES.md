# MXQ 4K (XT-MX4N-V03, RK3229) — Linux-шлюз. Сводка.

## Железо
- Плата XT-MX4N-V03 (2017/02/08), SoC Rockchip RK3229, NAND 8 ГБ Micron (сырой, прямые записи не грузятся — ставить только через Multitool/steP-nand), Wi-Fi SSV6051P (модуль ядра `ssv6x5x`).

## Состояние
- В NAND стоит Armbian 22.02 bullseye legacy (ядро 4.4.194-rk322x), rootfs 6.8G.
- Загружается без SD-карты. Первые 4 МБ NAND (заводской idbloader) трогать НЕЛЬЗЯ.

## Доступ
- SSH: **текущий IP по кабелю `root@192.168.3.59`** (Huawei WiFi AX2, роутер zahar, сеть 192.168.3.0/24), **пароль `<ПАРОЛЬ>`**. IP может меняться (DHCP) — искать по MAC `CA:58:70:3D:C4:96` / имени `rk322x-box`.
- С ноута: `sshpass -p <ПАРОЛЬ> ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@192.168.3.59 'команда'` (пароль sudo на ноуте тот же `<ПАРОЛЬ>`: `echo <ПАРОЛЬ> | sudo -S ...`)
- Wi-Fi: подключение `zahar` в NetworkManager **отключено (autoconnect no)** — шлюз работает СТРОГО по кабелю. Модуль `ssv6x5x` в /etc/modules (wlan0 появится после modprobe ssv6x5x при необходимости).
- MAC: wlan0 `3C:CF:5B:E3:42:CE`, eth0 `CA:58:70:3D:C4:96`, hostname `rk322x-box`.

## Что настроено
- **LED**: `/etc/systemd/system/led-static.service` — триггер none, горит статично (не мигает).
- **Xray 26.11.9 (arm32v7)** в `/opt/xray/`, конфиг `/usr/local/etc/xray/config.json`. Веб-интерфейс (порт 8080) теперь сам парсит ссылку `vless://...` из формы: пишет конфиг, валидирует (`xray run -test`) и делает `systemctl enable --now xray`. Прокси: SOCKS5 :10808, HTTP :10809.
- **Веб-интерфейс** `status-web.service`, python3 (ThreadingTCPServer), порт **8080** — http://192.168.3.59:8080. Парсит vless-ссылки и подписки Remnawave (JSON Happ-формат с заголовком `x-hwid`, фиксированный HWID в `/usr/local/etc/xray/hwid`; ВАЖНО: новые hwid панель блокирует лимитом устройств — не удалять файл). Несколько подписок → общий пул серверов, удаление подписки/всех, TCP-пинг серверов (12 потоков), кнопка Включить/Отключить VPN (активный сервер помнится в `/usr/local/etc/xray/active`). Исходник: `~/Projects/tvbox/status-web.py`. Подписка LaliVpn: `<SUBSCRIPTION_URL>`.
- **Режим VPN-шлюза (transparent)**: xray слушает dokodemo-door :12345 (followRedirect), `gw-rules.service` + `/usr/local/bin/gw-rules.sh` — iptables-legacy (переключён через update-alternatives, nf_tables на ядре 4.4 нет) REDIRECT TCP+DNS UDP в xray, MASQUERADE, ip_forward=1. Использование: на устройстве статический IP в подсети 192.168.3.0/24 со шлюзом 192.168.3.59 — весь трафик через VPN без настройки прокси.

## Планы/остатки
- Дождаться VLESS-ключ от пользователя → заполнить config.json → enable xray.
- Юзер не видел приставку в панели роутера (искать по имени rk322x-box или MAC 3C:CF:5B:E3:42:CE).
- Остались непрошитыми: Tronfy X2 (X2-V3.0), MXQ (HD18Q v1.0, Amlogic s805) — можно прошить SlimBox/Андроид.

## Файлы на ноуте (~/Projects/tvbox/)
- `Armbian_22.02.0-trunk_Rk322x-box_bullseye_legacy_4.4.194_minimal.img.xz` (+ копия в backup/), `multitool.img`, `xray-linux-32.zip` (ненужный i386), `Xray-linux-arm32-v7a.zip` (правильный), `xray-arm7/` (распакован), `status-web.py` (исходник веб-страницы).

## Роутер Huawei WiFi AX2 (WS7001-20) — исследование 2026-11-22 (read-only)
- Модель WS7001-20, прошивка 2.0.0.315 (HarmonyOS 2.0), панель http://192.168.3.1, admin/<ПАРОЛЬ>.
- **WAN: PPPoE** (username `<PPPOE_LOGIN>`, AC `sulu`, MRU 1492, MTU 1500, IP динамический; на момент исследования 91.109.143.190 = «прямой» IP шлюза). DNS провайдера 83.219.128.10/14 — UDP 53 к ним с приставки не отвечает (часть причин DNS-глюков).
- **IPv6 включён (SLAAC)** на роутере — при появлении глобальных префиксов клиенты начнут ходить в обход шлюза (шлюз только IPv4). Следить.
- Логин в API программно: SCRAM-HMAC-SHA256 (НЕстандартный порядок аргументов HMAC в прошивке Huawei!), CSRF из meta-тегов /html/index.html, обёртка {"data":...,"csrf":...}. Рабочий скрипт: /tmp/hwr/login.py (read-only GET). Блокировка после 3 неудачных логинов (~1 мин).
- Убрано из списков DNS провайдера; на шлюзе stubby→DoT 1.1.1.1.

## Оценка схемы «приставка между роутером и провайдером»
- Топология: ISP → (USB-ethernet на приставке) → приставка (PPPoE + NAT) → WAN-порт AX2 → LAN. Все устройства прозрачно идут через приставку, ничего вручную не прописывать.
- Технически возможно: NetworkManager умеет PPPoE, MTU 1492, NAT через gw-rules переставить на WAN→LAN.
- Минусы: (1) нужен USB-ethernet адаптер + драйвер под ядро 4.4 (asix/r8152 не факт); (2) пропускная способность NAT на RK3229 ~200-400 Мбит — потолок для всей квартиры; (3) зависание приставки = интернет мёртв у всех (нужен watchdog/LAN-fallback); (4) двойной NAT с AX2.
- Решение: оставить per-device шлюз (текущая схема), «между» делать только если надоест прописывать шлюз на новых устройствах.

## Обновление 2026-11-23: категории серверов + hysteria2
- В панели раздел «Серверы для категорий»: отдельный выбор сервера для Gemini/AI, Игр, YouTube. Сохраняется в /usr/local/etc/xray/routes.json, применяется сразу. «Авто» = через основной (Gemini-авто = сервер с GEMINI в имени).
- Antigravity (antigravity.google и т.д.) добавлен в GEMINI-домены — всегда идёт через GEMINI-сервер.
- Игры в списке только реально заблокированные: Roblox, Supercell (Brawl Stars/Clash Royale/CoC/Squad Busters), Garena, HoYoverse. Steam/Epic/PUBG/etc убраны — работают напрямую.
- Кнопка «Обновить подписки» в панели (фоновая, с прогрессом).
- Hysteria2: xray не умеет -> sing-box 1.11.15 сайдкар в /opt/sing-box, systemd sing-box.service. Панель парсит hysteria-only серверы из подписки ({"hy2": {...}} в servers.json), при выборе hy2-сервера пишет /usr/local/etc/sing-box/config.json (SOCKS 127.0.0.1:12080+n -> hysteria2) и xray шлёт трафик socks-outbound-ом туда. Сервер «🇩🇪 Германия (ДЛЯ ИГР) 🚀» — hy2.
