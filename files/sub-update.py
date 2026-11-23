#!/usr/bin/env python3
# Автообновление подписок (вызывается systemd timer-ом раз в час)
import sys, time

SRC = "/usr/local/bin/status-web.py"
src = open(SRC).read().replace('Server(("0.0.0.0", 8080), H).serve_forever()', "")
g = {}
exec(src, g)

log = lambda *a: print(time.strftime("%F %T"), *a)

BLOCKED_SOURCES = [
    ("https://raw.githubusercontent.com/dartraiden/no-russia-hosts/master/hosts.txt", "hosts"),
    ("https://raw.githubusercontent.com/1andrevich/Re-filter-lists/main/domains_all.lst", "plain"),
]
BLOCKED_DST = "/usr/local/etc/xray/blocked-extra.txt"

def update_blocked():
    import urllib.request
    doms = set()
    for url, fmt in BLOCKED_SOURCES:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        txt = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore")
        for l in txt.splitlines():
            l = l.strip().lower()
            if not l or l.startswith("#"):
                continue
            if fmt == "hosts":
                if l.startswith("0.0.0.0 "):
                    l = l.split()[1]
                elif " " in l:
                    continue
            # только валидные домены
            if "." in l and all(p.replace("-", "a").isalnum() for p in l.split(".")):
                doms.add(l)
    doms = sorted(doms)
    tmp = BLOCKED_DST + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(doms) + "\n")
    import os
    os.replace(tmp, BLOCKED_DST)
    log("OK список заблокированных:", len(doms), "доменов")

try:
    update_blocked()
except Exception as e:
    log("FAIL список заблокированных:", e)

for u in g["load_subs"]():
    try:
        n = g["fetch_sub_sync"](u)
        log("OK", u, "новых серверов:", n)
    except Exception as e:
        log("FAIL", u, e)
