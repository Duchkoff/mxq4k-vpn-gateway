#!/usr/bin/env python3
import subprocess, http.server, socketserver, json, urllib.parse, urllib.request, base64, os, re, uuid, socket, time, threading
from concurrent.futures import ThreadPoolExecutor

XRAY_CFG = "/usr/local/etc/xray/config.json"
SERVERS = "/usr/local/etc/xray/servers.json"
SUBS = "/usr/local/etc/xray/subs.json"
ACTIVE = "/usr/local/etc/xray/active"
HWID_FILE = "/usr/local/etc/xray/hwid"

JOBS = {}          # job_name -> (status_text, done)
JOBS_LOCK = threading.Lock()

def sh(c):
    try:
        return subprocess.check_output(c, shell=True, stderr=subprocess.DEVNULL, timeout=10).decode().strip()
    except Exception:
        return "?"

# ---------- vless parsing ----------

def parse_vless(link):
    link = link.strip()
    if not link.startswith("vless://"):
        return None
    rest = link[len("vless://"):]
    main, _, tail = rest.partition("?")
    query, _, frag = tail.partition("#")
    uid, _, hostport = main.partition("@")
    addr, _, port = hostport.rpartition(":")
    if not uid or not addr:
        return None
    uid = urllib.parse.unquote(uid)
    if addr in ("0.0.0.0", "") or uid.startswith("00000000"):
        return None  # заглушки подписок
    q = urllib.parse.parse_qs(query)
    name = urllib.parse.unquote(frag) or addr
    sec = q.get("security", ["none"])[0]
    user = {"id": uid, "encryption": q.get("encryption", ["none"])[0]}
    if q.get("flow"):
        user["flow"] = q["flow"][0]
    out = {
        "protocol": "vless",
        "settings": {"vnext": [{"address": addr, "port": int(port or 443), "users": [user]}]},
        "streamSettings": {"network": q.get("type", ["tcp"])[0], "security": sec},
    }
    if sec == "reality":
        out["streamSettings"]["realitySettings"] = {
            "serverName": q.get("sni", ["www.microsoft.com"])[0],
            "publicKey": q.get("pbk", [""])[0],
            "shortId": q.get("sid", [""])[0],
        }
    elif sec == "tls":
        out["streamSettings"]["tlsSettings"] = {"serverName": q.get("sni", [addr])[0]}
    return {"name": name, "link": link, "outbound": out}

def decode_subscription(text):
    text = text.strip()
    if "://" not in text.split("\n")[0] and len(text) > 40:
        try:
            pad = "=" * (-len(text) % 4)
            text = base64.urlsafe_b64decode(text + pad).decode("utf-8", "ignore")
        except Exception:
            pass
    return [l.strip() for l in re.split(r"[\r\n]+", text) if l.strip().startswith("vless://")]

# ---------- storage ----------

def load_servers():
    try:
        return json.load(open(SERVERS))
    except Exception:
        return []

def save_servers(s):
    tmp = SERVERS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=1)
    os.replace(tmp, SERVERS)

def load_subs():
    try:
        return json.load(open(SUBS))
    except Exception:
        return []

def save_subs(s):
    with open(SUBS, "w") as f:
        json.dump(s, f, indent=1)

def get_active():
    """-> (index, mode) ; index=-1 = прямой доступ"""
    try:
        t = open(ACTIVE).read().strip()
        if t == "-1" or not t:
            return -1, "bypass"
        i, _, m = t.partition(":")
        return int(i), (m or "bypass")
    except Exception:
        return -1, "bypass"

def set_active(i, mode="bypass"):
    open(ACTIVE, "w").write("-1" if i < 0 else (str(i) + ":" + mode))

def get_hwid():
    try:
        return open(HWID_FILE).read().strip()
    except Exception:
        h = str(uuid.uuid4())
        open(HWID_FILE, "w").write(h)
        return h

def _test_and_apply(cfg):
    tmp = XRAY_CFG + ".tmp.json"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    r = subprocess.run(["/opt/xray/xray", "run", "-test", "-c", tmp], capture_output=True, timeout=30)
    if r.returncode != 0:
        os.remove(tmp)
        raise ValueError("xray не принял конфиг: " + (r.stderr.decode(errors="ignore") or r.stdout.decode(errors="ignore"))[-200:])
    os.replace(tmp, XRAY_CFG)
    subprocess.run("systemctl enable xray && systemctl restart xray", shell=True, capture_output=True)

# русские сервисы — ВСЕГДА напрямую (геоблоки: яндекс-музыка, банки и т.д.)
RU_DIRECT = ["geosite:yandex", "geosite:vk", "geosite:ok", "geosite:mailru",
             "geosite:avito", "geosite:ozon", "geosite:wildberries", "geosite:kinopoisk",
             "geosite:sberbank", "domain:tinkoff.ru", "domain:tinkoff.net", "domain:gosuslugi.ru"]
# gemini/AI — на отдельный сервер (если в пуле есть сервер с GEMINI в имени)
GEMINI_DOMAINS = ["domain:gemini.google.com", "domain:aistudio.google.com", "domain:generativelanguage.googleapis.com",
                  "domain:alkalicore-pa.clients6.google.com", "domain:alkalimakersuite-pa.clients6.google.com",
                  "domain:proactivebackend-pa.googleapis.com", "domain:bard.google.com", "domain:deepmind.com",
                  "domain:deepmind.google", "domain:labs.google", "domain:notebooklm.google.com", "domain:googlelabs.com",
                  "domain:antigravity.google", "domain:antigravity.google.com", "domain:antigravity.googleapis.com"]
# youtube — отдельный выбор сервера
YOUTUBE_DOMAINS = ["geosite:youtube", "domain:googlevideo.com", "domain:ytimg.com", "domain:ytimg.net",
                   "domain:ytimg.co", "domain:ggpht.com"]
# игры — отдельный выбор сервера (только реально заблокированные/режущие РФ)
GAMES_DOMAINS = ["domain:roblox.com", "domain:rbxcdn.com", "domain:rbxcdnlive.com", "domain:rbxcloud.com",
                 "domain:supercell.com", "domain:supercellid.com", "domain:clashofclans.com", "domain:clashroyale.com",
                 "domain:brawlstars.com", "domain:haydaygame.com", "domain:boombeachgame.com", "domain:squadbusters.com",
                 "domain:garena.com", "domain:garenanow.com", "domain:gcloudcs.com",
                 "domain:hoyoverse.com", "domain:giitkgame.com"]

# категории с отдельным выбором сервера: имя -> домены
CATEGORIES = {"gemini": GEMINI_DOMAINS, "games": GAMES_DOMAINS, "youtube": YOUTUBE_DOMAINS}
CATEGORY_TITLES = {"gemini": "Gemini / AI", "games": "Игры", "youtube": "YouTube"}
ROUTES = "/usr/local/etc/xray/routes.json"  # {"gemini": idx|-1, "games": ..., "youtube": ...}

def load_routes():
    try:
        r = json.load(open(ROUTES))
        return {c: int(r.get(c, -1)) for c in CATEGORIES}
    except Exception:
        return {c: -1 for c in CATEGORIES}

def save_routes(r):
    tmp = ROUTES + ".tmp"
    with open(tmp, "w") as f:
        json.dump(r, f)
    os.replace(tmp, ROUTES)
# заблокированное/замедленное — через VPN в режиме "обход" (youtube/игры/gemini вынесены в категории выше)
BLOCKED_BASE = ["geosite:facebook", "geosite:instagram", "geosite:twitter",
                "geosite:discord", "geosite:openai", "geosite:github", "geosite:spotify",
                "geosite:telegram", "geosite:whatsapp", "domain:googleusercontent.com"]
# IP-сети сервисов, которые провайдер режет/блокирует (для трафика без SNI/ECH)
BLOCKED_IPS = ["geoip:telegram", "geoip:twitter", "geoip:facebook"]
# российские IP — напрямую (ускоряет RU-сайты, до которых дошли по IP)
RU_IPS = ["geoip:ru"]
# сами ушли из РФ / режут российских пользователей
BLOCKED_LEFT = ["domain:soundcloud.com", "domain:sndcdn.com", "domain:netflix.com", "domain:nflxvideo.net",
                "domain:nflximg.net", "domain:crunchyroll.com", "domain:crunchyrollcdn.com", "domain:hbo.com",
                "domain:max.com", "domain:disneyplus.com", "domain:dazn.com", "domain:twitch.tv", "domain:ttvnw.net"]
# список заблокированных РКН ресурсов (no-russia-hosts), обновляется sub-update
BLOCKED_EXTRA_FILE = "/usr/local/etc/xray/blocked-extra.txt"

def blocked_domains():
    doms = list(BLOCKED_LEFT)
    try:
        with open(BLOCKED_EXTRA_FILE) as f:
            for line in f:
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    doms.append("domain:" + line)
    except FileNotFoundError:
        pass
    return BLOCKED_BASE + doms

def find_gemini_idx(servers):
    for i, s in enumerate(servers):
        if "GEMINI" in s["name"].upper():
            return i
    return -1

# ---------- hysteria2 через sing-box сайдкар ----------
# xray не умеет hysteria2 -> поднимаем sing-box с SOCKS-инбаундами на 127.0.0.1,
# xray шлёт такой трафик socks-outbound-ом на нужный порт
HY2_PORT_BASE = 12080
SINGBOX_CFG = "/usr/local/etc/sing-box/config.json"

def ob_for(s, tag, plan):
    """xray-outbound для сервера; hysteria2 -> SOCKS на sing-box (записывается в plan)"""
    if "hy2" in s:
        port = HY2_PORT_BASE + len(plan)
        plan[tag] = (port, s["hy2"])
        return {"protocol": "socks",
                "settings": {"servers": [{"address": "127.0.0.1", "port": port}]},
                "tag": tag}
    return dict(s["outbound"], tag=tag)

def write_singbox(plan):
    if not plan:
        subprocess.run("systemctl stop sing-box", shell=True, capture_output=True)
        return
    inbounds, outbounds, rules = [], [], []
    for tag, (port, hy2) in plan.items():
        inbounds.append({"type": "socks", "tag": "in-" + tag,
                         "listen": "127.0.0.1", "listen_port": port})
        outbounds.append({"type": "hysteria2", "tag": "hy-" + tag,
                          "server": hy2["address"], "server_port": hy2["port"],
                          "password": hy2["password"],
                          "tls": {"enabled": True,
                                  "server_name": hy2.get("sni") or hy2["address"],
                                  "alpn": ["h3"]}})
        rules.append({"inbound": "in-" + tag, "outbound": "hy-" + tag})
    outbounds.append({"type": "direct", "tag": "direct"})
    cfg = {"log": {"level": "warn"}, "inbounds": inbounds,
           "outbounds": outbounds, "route": {"rules": rules, "final": "direct"}}
    tmp = SINGBOX_CFG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=1)
    os.replace(tmp, SINGBOX_CFG)
    subprocess.run("systemctl enable sing-box && systemctl restart sing-box",
                   shell=True, capture_output=True)

def full_config(main_s, mode, gemini_s=None, routes=None):
    """mode: 'full' — всё через VPN; 'bypass' — только заблокированное через VPN, остальное напрямую.
    main_s=None — прямой доступ (VPN выкл). routes: {категория: idx|-1}.
    Возвращает (config, plan) — plan: {tag: (port, hy2)} для sing-box."""
    routes = routes or {}
    outbounds = []
    rules = []
    plan = {}
    if gemini_s is not None:
        gemini_tag = "gemini"
        outbounds.append(ob_for(gemini_s, "gemini", plan))
    else:
        gemini_tag = "main"
    outbounds.append({"protocol": "freedom", "tag": "direct"})
    if main_s is None:
        outbounds.append({"protocol": "freedom", "tag": "main"})
    else:
        outbounds.append(ob_for(main_s, "main", plan))

    # категории с выбранным сервером -> отдельный outbound; иначе fallback
    pool = load_servers()
    for cat, doms in CATEGORIES.items():
        tag = gemini_tag if cat == "gemini" else None  # дефолт gemini: авто-сервер GEMINI, иначе main
        idx = routes.get(cat, -1)
        if idx >= 0 and idx < len(pool):
            tag = "cat:" + cat
            outbounds.append(ob_for(pool[idx], tag, plan))
        if tag is None:
            tag = "main"  # games/youtube без выбора -> основной
        rules.append({"type": "field", "domain": doms, "outboundTag": tag})

    rules.append({"type": "field", "domain": RU_DIRECT, "outboundTag": "direct"})
    if mode == "bypass":
        rules.append({"type": "field", "domain": blocked_domains(), "outboundTag": "main"})
        rules.append({"type": "field", "ip": BLOCKED_IPS, "outboundTag": "main"})
        first = "direct"
    else:
        first = "main"
    rules.append({"type": "field", "ip": RU_IPS, "outboundTag": "direct"})
    # первым должен быть дефолтный outbound
    outbounds.sort(key=lambda o: 0 if o["tag"] == first else 1)

    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {"listen": "0.0.0.0", "port": 10808, "protocol": "socks", "settings": {"udp": True}},
            {"listen": "0.0.0.0", "port": 10809, "protocol": "http"},
            {
                "listen": "0.0.0.0", "port": 12345, "protocol": "dokodemo-door",
                "settings": {"network": "tcp,udp", "followRedirect": True},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            },
        ],
        "outbounds": outbounds,
        "routing": {"domainStrategy": "IPIfNonMatch", "rules": rules},
    }, plan

def apply_server(i, mode="bypass"):
    servers = load_servers()
    if i < 0 or i >= len(servers):
        raise ValueError("нет такого сервера")
    gi = find_gemini_idx(servers)
    gemini_s = servers[gi] if (gi >= 0 and gi != i) else None
    cfg, plan = full_config(servers[i], mode, gemini_s, load_routes())
    write_singbox(plan)
    _test_and_apply(cfg)
    set_active(i, mode)
    return servers[i]["name"]

def apply_direct():
    """xray продолжает слушать 12345/10808/10809, но гонит трафик напрямую (без VPN)"""
    cfg, plan = full_config(None, "full", None, load_routes())
    write_singbox(plan)
    _test_and_apply(cfg)
    set_active(-1)

def disconnect():
    try:
        apply_direct()
    except Exception:
        # если сменить конфиг не вышло — хотя бы не оставлять повисшим
        subprocess.run(["systemctl", "disable", "--now", "xray"], capture_output=True)
        set_active(-1)

def reconnect():
    i, mode = get_active()
    if i < 0:
        raise ValueError("нечего включать — выбери сервер")
    servers = load_servers()
    if i >= len(servers):
        raise ValueError("сервер из списка пропал")
    return apply_server(i, mode) + (" (обход)" if mode == "bypass" else " (весь трафик)")

def add_link(link, sub_name=""):
    s = parse_vless(link)
    if not s:
        raise ValueError("не похоже на vless:// ссылку")
    s["sub"] = sub_name
    servers = load_servers()
    for e in servers:
        if e["link"] == link:
            return servers.index(e)
    servers.append(s)
    save_servers(servers)
    return len(servers) - 1

# ---------- subscription fetch (background) ----------

def fetch_sub_sync(url):
    url = url.strip()
    text = None
    last_err = ""
    for ua in ("Happ/1.0", "v2rayNG/1.8.5"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "x-hwid": get_hwid()})
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode("utf-8", "ignore")
            break
        except Exception as e:
            last_err = str(e)
    if text is None:
        raise ValueError("не скачалась: " + last_err)
    subs = load_subs()
    if url not in subs:
        subs.append(url)
        save_subs(subs)
    servers = load_servers()
    have = {e["link"] for e in servers}
    n = 0
    stripped = text.strip()
    if stripped.startswith("[") or stripped.startswith("{"):
        d = json.loads(stripped)
        if isinstance(d, dict):
            d = [d]
        for o in d:
            remark = (o.get("remarks") or "").strip()
            remark = re.sub(r"\s*➪\s*", " ", remark)
            remark = re.sub(r"\s+", " ", remark).strip()
            cands = [b for b in o.get("outbounds", [])
                     if b.get("protocol") == "vless" and b.get("settings", {}).get("vnext")]
            if not cands:
                # hysteria2-only сервер -> идёт через sing-box сайдкар
                hb = next((b for b in o.get("outbounds", []) if b.get("protocol") == "hysteria"), None)
                if hb:
                    st = hb.get("streamSettings", {})
                    hy = st.get("hysteriaSettings", {})
                    tls = st.get("tlsSettings", {})
                    hy2 = {"address": hb["settings"]["address"],
                           "port": int(hb["settings"]["port"]),
                           "password": hy.get("auth", ""),
                           "sni": tls.get("serverName", "")}
                    key = json.dumps(hb, sort_keys=True)
                    if key not in have:
                        servers.append({"name": remark or hy2["address"],
                                        "link": key, "hy2": hy2, "sub": url})
                        have.add(key)
                        n += 1
                continue
            ob = cands[0]
            rs = ob.get("streamSettings", {})
            if "realitySettings" in rs:
                rs["realitySettings"] = {k: v for k, v in rs["realitySettings"].items()
                                         if k in ("serverName", "publicKey", "shortId", "spiderX")}
            ob2 = {k: ob[k] for k in ("protocol", "settings", "streamSettings") if k in ob}
            key = json.dumps(ob2, sort_keys=True)
            if key in have:
                continue
            servers.append({"name": remark or ob2["settings"]["vnext"][0]["address"],
                            "link": key, "outbound": ob2, "sub": url})
            have.add(key)
            n += 1
        if n == 0 and not any(b.get("protocol") in ("vless", "hysteria") for o in d for b in o.get("outbounds", [])):
            raise ValueError("подписка не дала серверов (лимит устройств? блокировка HWID?)")
    else:
        links = [l for l in decode_subscription(text) if parse_vless(l)]
        if not links:
            raise ValueError("в подписке нет рабочих vless-ссылок")
        for l in links:
            if l in have:
                continue
            s = parse_vless(l)
            s["sub"] = url
            servers.append(s)
            have.add(l)
            n += 1
    save_servers(servers)
    return n

def refresh_all_async():
    """Обновить все подписки разом (кнопка в панели)"""
    def job():
        with JOBS_LOCK:
            JOBS["sub"] = ("Обновляю все подписки...", False)
        total, errs = 0, []
        for u in load_subs():
            try:
                total += fetch_sub_sync(u)
            except Exception as e:
                errs.append(str(e)[:80])
        msg = "OK: новых серверов: " + str(total)
        if errs:
            msg += "; ошибки: " + "; ".join(errs)
        with JOBS_LOCK:
            JOBS["sub"] = (msg, True)
    threading.Thread(target=job, daemon=True).start()

def fetch_sub_async(url):
    def job():
        with JOBS_LOCK:
            JOBS["sub"] = ("Загружаю подписку...", False)
        try:
            n = fetch_sub_sync(url)
            with JOBS_LOCK:
                JOBS["sub"] = ("OK: загружено серверов: " + str(n), True)
        except Exception as e:
            with JOBS_LOCK:
                JOBS["sub"] = ("Ошибка загрузки подписки: " + str(e), True)
    threading.Thread(target=job, daemon=True).start()

def job_status():
    with JOBS_LOCK:
        return JOBS.get("sub")

# ---------- ping ----------

def ping_one(s):
    if "hy2" in s:
        host, port = s["hy2"]["address"], s["hy2"]["port"]
    else:
        v = s["outbound"]["settings"]["vnext"][0]
        host, port = v["address"], int(v["port"])
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=3):
            return str(int((time.time() - t0) * 1000))
    except Exception:
        return "—"

def ping_all_async():
    def job():
        with JOBS_LOCK:
            JOBS["ping"] = ("Пинганую серверы...", False)
        servers = load_servers()
        with ThreadPoolExecutor(max_workers=12) as ex:
            results = list(ex.map(ping_one, servers))
        for s, r in zip(servers, results):
            s["ping"] = r
        save_servers(servers)
        ok = sum(1 for r in results if r != "—")
        with JOBS_LOCK:
            JOBS["ping"] = ("OK: отвечают {}/{}".format(ok, len(servers)), True)
    threading.Thread(target=job, daemon=True).start()

# ---------- page ----------

def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def page(msg="", msgok=True):
    ip = sh("hostname -I | awk '{print $1}'")
    eth = sh("ip -4 addr show eth0 2>/dev/null | grep inet | awk '{print $2}'")
    xray = sh("systemctl is-active xray")
    up = sh("uptime -p")
    servers = load_servers()
    subs = load_subs()
    act, cur_mode = get_active()
    running = any(not j[1] for j in [job_status() or ("", True)] )
    auto = '<meta http-equiv="refresh" content="3">' if running else ""

    connected = (0 <= act < len(servers)) and xray == "active"
    cur = ""
    if 0 <= act < len(servers):
        cur = esc(servers[act]["name"])
    if connected:
        xstate, xcolor = "подключён", "#4d7"
    elif xray == "active":
        xstate, xcolor = "прямой доступ (VPN выкл)", "#fc3"
    else:
        xstate, xcolor = esc(xray), "#f66"

    # toggle button
    if connected:
        toggle = '<form class=inline method=post><button class="danger big" name=disconnect value=1>Отключить VPN</button></form>'
    else:
        toggle = '<form class=inline method=post><button class="okbtn big" name=reconnect value=1>Включить VPN</button></form>'

    # subscription chips
    chips = ""
    for u in subs:
        short = u.rstrip("/").split("/")[-1][:12]
        cnt = sum(1 for s in servers if s.get("sub") == u)
        chips += ('<div class=card style="background:#181c22;border-color:#2a3442"><div class=row><div><b>' +
                  esc(short) + '</b> <small>' + str(cnt) + ' серверов</small><br><small>' + esc(u) + '</small></div>' +
                  '<form class=inline method=post><button name=delsub value="' + esc(u) + '">Удалить</button></form></div></div>')
    if not subs:
        chips = '<p class=hint>Подписок нет.</p>'

    # server cards
    cards = ""
    for i, s in enumerate(servers):
        if "hy2" in s:
            addr = s["hy2"]["address"] + ":" + str(s["hy2"]["port"]) + " (hy2)"
        else:
            v = s["outbound"]["settings"]["vnext"][0]
            addr = v["address"] + ":" + str(v["port"])
        style = "background:#173327;border-color:#4d7" if (i == act and connected) else "background:#1d1d1d;border-color:#333"
        ping = s.get("ping")
        badge = ('<span class=badge>ПОДКЛЮЧЕНО</span>' if (i == act and connected)
                 else '<button name=use value="' + str(i) + ':bypass">Обход</button> <button name=use value="' + str(i) + ':full">Весь трафик</button>')
        right = badge + ('<br><small class=ping>' + esc(ping) + ' мс</small>' if ping else "")
        cards += ('<div class=card style="' + style + '"><div class=row><div><b>' + esc(s["name"]) +
                  '</b><br><small>' + esc(addr) + '</small></div>' +
                  '<form class=inline method=post>' + right + '</form></div></div>')
    if not servers:
        cards = '<p class=hint>Серверов нет — добавь ссылку или подписку ниже.</p>'

    js = job_status()
    jobhtml = ('<p class="job">' + esc(js[0]) + '</p>') if js else ""

    # category route selectors
    routes = load_routes()
    cats_html = ""
    for cat in CATEGORIES:
        sel = ('<select name="srv" style="width:100%;padding:10px;margin:6px 0;background:#1b1b1b;color:#eee;'
               'border:1px solid #444;border-radius:8px;font-size:14px">'
               '<option value="-1">Авто (основной сервер)</option>')
        for i, s in enumerate(servers):
            cur_sel = " selected" if routes.get(cat, -1) == i else ""
            nm = esc(s["name"])
            if s.get("ping"):
                nm += " · " + esc(str(s["ping"])) + " мс"
            sel += '<option value="' + str(i) + '"' + cur_sel + '>' + nm + '</option>'
        sel += '</select>'
        cats_html += ('<div class=card style="background:#181c22;border-color:#2a3442"><b>' +
                      CATEGORY_TITLES[cat] + '</b>' +
                      '<form class=inline method=post style="margin-top:6px"><input type=hidden name=cat value="' +
                      cat + '">' + sel +
                      '<button type=submit>Сохранить</button></form></div>')

    return """<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">""" + auto + """
<title>MXQ gateway</title>
<style>
body{font-family:system-ui,sans-serif;max-width:640px;margin:0 auto;background:#111;color:#eee;padding:16px}
h1{font-size:22px}h2{font-size:17px;margin:22px 0 8px}
table{border-collapse:collapse}td{padding:3px 18px 3px 0;color:#bbb}td b{color:#eee;font-weight:600}
.ok{background:#173327;border-radius:8px;padding:10px 14px}
.err{background:#3a1717;border-radius:8px;padding:10px 14px}
.job{background:#1a2333;border-radius:8px;padding:10px 14px}
.hint{color:#888}
.card{border:1px solid #333;border-radius:10px;padding:12px 14px;margin:8px 0}
.row{display:flex;justify-content:space-between;align-items:center;gap:10px}
small{color:#999}.ping{color:#7fd}
.badge{background:#1d5c38;color:#7fe0a8;border-radius:20px;padding:4px 12px;font-size:12px;white-space:nowrap}
input{width:100%;padding:12px;margin:6px 0;box-sizing:border-box;font-size:15px;border-radius:8px;border:1px solid #444;background:#1b1b1b;color:#eee}
button{padding:9px 18px;background:#2b6cb0;color:#fff;border:0;border-radius:8px;font-size:14px;cursor:pointer;white-space:nowrap}
button.big{width:100%;padding:13px;font-size:16px}
button.danger{background:#b03a2e}
button.okbtn{background:#1d8a4e}
form.inline{margin:0}
details{background:#181818;border-radius:8px;padding:10px 14px;margin:10px 0}
code{background:#222;padding:1px 5px;border-radius:4px}
</style>
<h1>MXQ 4K шлюз</h1>""" + (('<p class="' + ("ok" if msgok else "err") + '">' + esc(msg) + '</p>') if msg else "") + jobhtml + """
<table>
<tr><td>IP (кабель)</td><td><b>""" + esc(eth) + """</b></td></tr>
<tr><td>Xray</td><td><b style="color:""" + xcolor + '">' + xstate + """</b></td></tr>
<tr><td>Текущий сервер</td><td>""" + cur + ((" (" + ("обход" if cur_mode == "bypass" else "весь трафик") + ")") if connected else "") + """</td></tr>
<tr><td>Серверов в пуле</td><td>""" + str(len(servers)) + """</td></tr>
<tr><td>Uptime</td><td>""" + esc(up) + """</td></tr>
</table>
<p class=hint>Прокси SOCKS5: <b>""" + esc(ip) + """:10808</b> · HTTP: <b>""" + esc(ip) + """:10809</b></p>
""" + toggle + """
<h2>Подписки</h2>
""" + chips + """
<form method=post><input name=link placeholder="vless://... или https://...подписка" autocomplete=off>
<button class=big type=submit>Добавить</button></form>
<form class=inline method=post style="margin-top:8px"><button class="big" name=refresh value=1>Обновить подписки</button></form>
<form class=inline method=post style="margin-top:8px"><button class="danger big" name=clear value=1>Удалить все серверы и подписки</button></form>
<h2>Серверы</h2>
<form class=inline method=post style="margin-bottom:10px"><button name=ping value=1>Тест пинга (все)</button></form>
""" + cards + """
<h2>Серверы для категорий</h2>
<p class=hint>Отдельный сервер для Gemini, игр (Roblox, Supercell, Steam...) и YouTube. «Авто» = идут через основной сервер.</p>
""" + cats_html + """
<details><summary>Настройка шлюза для устройства</summary>
<p>Чтобы <b>весь</b> трафик устройства шёл через VPN (как в Happ, но для всей системы):</p>
<ol>
<li>На устройстве задай статический IP: адрес <code>192.168.3.x</code> (любой свободный, не .59), маска <code>255.255.255.0</code>.</li>
<li>Шлюз (gateway): <code>""" + esc(ip) + """</code> — это IP приставки.</li>
<li>DNS: любой, например <code>1.1.1.1</code> (DNS всё равно перехватывается туннелем).</li>
</ol>
<p>Проверка: открой на устройстве <code>2ip.ru</code> — страна должна смениться на страну VPN-сервера.</p>
<p>На Android: Настройки → Wi-Fi → сеть zahar → Дополнительно → IP-настройки «Статический».<br>
На Windows: Параметры адаптера → IPv4 → вручную.</p>
<p>Чтобы вернуть обычный интернет — верни DHCP (автоматический IP) на устройстве.</p>
</details>
"""

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self._send(page())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        data = urllib.parse.parse_qs(self.rfile.read(length).decode())
        msg, ok = "", True
        try:
            if data.get("disconnect"):
                disconnect()
                msg = "VPN отключён"
            elif data.get("reconnect"):
                msg = "Подключено: " + reconnect()
            elif data.get("delsub"):
                u = data.get("delsub", [""])[0]
                subs = load_subs()
                if u in subs:
                    subs.remove(u)
                    save_subs(subs)
                servers = load_servers()
                removed = sum(1 for s in servers if s.get("sub") == u)
                servers = [s for s in servers if s.get("sub") != u]
                save_servers(servers)
                act, cur_mode = get_active()
                set_active(-1 if act >= len(servers) else act)
                msg = "Подписка удалена, серверов убрано: " + str(removed)
            elif data.get("refresh"):
                refresh_all_async()
                msg = "Подписки обновляются в фоне, страница обновится"
            elif data.get("clear"):
                save_servers([])
                save_subs([])
                disconnect()
                msg = "Всё очищено"
            elif data.get("ping"):
                ping_all_async()
                msg = "Пинг запущен, страница обновится"
            elif data.get("cat"):
                cat = data.get("cat", [""])[0]
                if cat not in CATEGORIES:
                    raise ValueError("неизвестная категория")
                routes = load_routes()
                routes[cat] = int(data.get("srv", ["-1"])[0])
                save_routes(routes)
                act, _ = get_active()
                if act >= 0:
                    reconnect()
                    msg = CATEGORY_TITLES[cat] + ": сервер сохранён и применён"
                else:
                    msg = CATEGORY_TITLES[cat] + ": сервер сохранён (применится при включении VPN)"
            elif data.get("use"):
                v = data["use"][0]
                si, _, m = v.partition(":")
                name = apply_server(int(si), m or "bypass")
                msg = "Подключено: " + name + (" (обход)" if (m or "bypass") == "bypass" else " (весь трафик)")
            elif data.get("link"):
                link = data.get("link", [""])[0].strip()
                if link.startswith("http"):
                    fetch_sub_async(link)
                    msg = "Подписка загружается в фоне..."
                else:
                    i = add_link(link)
                    msg = "Добавлено и подключено: " + apply_server(i)
        except Exception as e:
            msg, ok = str(e), False
        self._send(page(msg, ok))

    def _send(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *a):
        pass

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

Server(("0.0.0.0", 8080), H).serve_forever()
