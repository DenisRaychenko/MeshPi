"""Сповіщення поза браузером: Telegram, ntfy і вебхук. Налаштування (з токенами) лежать лише на сервері у notify.json
(права 600) і браузеру не віддаються: він бачить лише «токен задано». Усе, що відправляється, іде короткими запитами з
таймаутом, без переходів за редиректами, і не блокує панель (див. send_async)."""
import json
import os
import re
import threading
import urllib.error
import urllib.request

KINDS = {  # вид події -> (підпис, чи сповіщати типово)
    "new": ("новий вузол", False), "fav_off": ("обраний пропав", True), "fav_on": ("обраний повернувся", True),
    "bat_low": ("низький заряд", True), "chan_high": ("перевантажений ефір", True), "reboot": ("вузол перезапустився", True),
    "link_lost": ("зв'язок із вузлом втрачено", True), "link_ok": ("зв'язок відновлено", True),
}
RULES = {"battery": 20, "chan": 40}      # пороги: % заряду (0 вимикає) і % зайнятості каналу (0 вимикає)
TELEGRAM_TOKEN = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,50}$")
TELEGRAM_CHAT = re.compile(r"^(-?\d{1,15}|@[A-Za-z0-9_]{4,32})$")
URL = re.compile(r"^https?://[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(:[0-9]{1,5})?(/[A-Za-z0-9._~%/+-]{0,200})?$")
TOPIC = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SECRET_RE = re.compile(r"^[ -~]{0,300}$")
TIMEOUT = 8


def defaults():
    return {"telegram": {"on": False, "token": "", "chat": ""}, "ntfy": {"on": False, "url": "", "topic": "", "token": ""},
            "webhook": {"on": False, "url": "", "token": ""}, "kinds": {k: v[1] for k, v in KINDS.items()}, "rules": dict(RULES)}


def load(path):
    cfg = defaults()
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return cfg
    if not isinstance(raw, dict):
        return cfg
    for ch in ("telegram", "ntfy", "webhook"):
        if isinstance(raw.get(ch), dict):
            for k in cfg[ch]:
                if k == "on":
                    cfg[ch][k] = bool(raw[ch].get(k))
                elif isinstance(raw[ch].get(k), str):
                    cfg[ch][k] = raw[ch][k]
    if isinstance(raw.get("kinds"), dict):
        for k in cfg["kinds"]:
            if isinstance(raw["kinds"].get(k), bool):
                cfg["kinds"][k] = raw["kinds"][k]
    if isinstance(raw.get("rules"), dict):
        for k in cfg["rules"]:
            v = raw["rules"].get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 100:
                cfg["rules"][k] = int(v)
    return cfg


def save(path, cfg):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def public(cfg):
    """Для браузера: без токенів, лише прапорець «задано»."""
    t, n, w = cfg["telegram"], cfg["ntfy"], cfg["webhook"]
    return {"telegram": {"on": t["on"], "chat": t["chat"], "token_set": bool(t["token"])},
            "ntfy": {"on": n["on"], "url": n["url"], "topic": n["topic"], "token_set": bool(n["token"])},
            "webhook": {"on": w["on"], "url": w["url"], "token_set": bool(w["token"])},
            "kinds": cfg["kinds"], "rules": cfg["rules"], "labels": {k: v[0] for k, v in KINDS.items()}}


def update(cfg, body):
    """Нові налаштування з тіла запиту -> (cfg, помилка). Токен, якого немає в запиті чи він порожній, лишається;
    поле clear:[канал,…] стирає канал."""
    if not isinstance(body, dict):
        return cfg, "очікується об'єкт"
    new = json.loads(json.dumps(cfg))
    for ch in body.get("clear") or []:
        if ch in ("telegram", "ntfy", "webhook"):
            new[ch] = defaults()[ch]
    t = body.get("telegram")
    if isinstance(t, dict):
        if "on" in t:
            new["telegram"]["on"] = bool(t["on"])
        if str(t.get("token") or ""):
            if not TELEGRAM_TOKEN.match(str(t["token"])):
                return cfg, "токен Telegram має вигляд 123456789:AA… (від @BotFather)"
            new["telegram"]["token"] = str(t["token"])
        if "chat" in t:
            chat = str(t["chat"] or "").strip()
            if chat and not TELEGRAM_CHAT.match(chat):
                return cfg, "chat id Telegram: число (може бути з мінусом) або @канал"
            new["telegram"]["chat"] = chat
        if new["telegram"]["on"] and not (new["telegram"]["token"] and new["telegram"]["chat"]):
            return cfg, "Telegram: потрібні токен і chat id"
    n = body.get("ntfy")
    if isinstance(n, dict):
        if "on" in n:
            new["ntfy"]["on"] = bool(n["on"])
        if "url" in n:
            url = str(n["url"] or "").strip().rstrip("/")
            if url and not URL.match(url):
                return cfg, "ntfy: адреса має вигляд https://ntfy.sh або http://хост:порт"
            new["ntfy"]["url"] = url
        if "topic" in n:
            topic = str(n["topic"] or "").strip()
            if topic and not TOPIC.match(topic):
                return cfg, "ntfy: тема лише з літер, цифр, - і _"
            new["ntfy"]["topic"] = topic
        if str(n.get("token") or ""):
            if not SECRET_RE.match(str(n["token"])):
                return cfg, "ntfy: токен містить недозволені символи"
            new["ntfy"]["token"] = str(n["token"])
        if new["ntfy"]["on"] and not (new["ntfy"]["url"] and new["ntfy"]["topic"]):
            return cfg, "ntfy: потрібні адреса й тема"
    w = body.get("webhook")
    if isinstance(w, dict):
        if "on" in w:
            new["webhook"]["on"] = bool(w["on"])
        if "url" in w:
            url = str(w["url"] or "").strip()
            if url and not URL.match(url):
                return cfg, "вебхук: адреса має вигляд http(s)://хост[:порт][/шлях]"
            new["webhook"]["url"] = url
        if str(w.get("token") or ""):
            if not SECRET_RE.match(str(w["token"])):
                return cfg, "вебхук: токен містить недозволені символи"
            new["webhook"]["token"] = str(w["token"])
        if new["webhook"]["on"] and not new["webhook"]["url"]:
            return cfg, "вебхук: потрібна адреса"
    if isinstance(body.get("kinds"), dict):
        for k, v in body["kinds"].items():
            if k in KINDS and isinstance(v, bool):
                new["kinds"][k] = v
    if isinstance(body.get("rules"), dict):
        for k, v in body["rules"].items():
            if k in RULES:
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100:
                    return cfg, f"поріг «{k}»: число від 0 до 100"
                new["rules"][k] = int(v)
    return new, None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _post(url, data, headers, opener=None):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with (opener or _opener).open(req, timeout=TIMEOUT) as r:
            return 200 <= r.status < 300, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, str(getattr(e, "reason", e))[:100]


def send(cfg, title, text, level="info", now=None, opener=None):
    """Надіслати в усі ввімкнені канали -> [(канал, ok, пояснення)]. Токени в пояснення не потрапляють."""
    out = []
    body = f"{title}\n{text}" if title else text
    t = cfg["telegram"]
    if t["on"] and t["token"] and t["chat"]:
        ok, why = _post(f"https://api.telegram.org/bot{t['token']}/sendMessage",
                        json.dumps({"chat_id": t["chat"], "text": body, "disable_notification": level == "info"}).encode(),
                        {"Content-Type": "application/json"}, opener)
        out.append(("telegram", ok, why))
    n = cfg["ntfy"]
    if n["on"] and n["url"] and n["topic"]:
        h = {"Priority": "high" if level in ("warn", "crit") else "default", "Content-Type": "text/plain; charset=utf-8"}
        if n["token"]:
            h["Authorization"] = "Bearer " + n["token"]
        ok, why = _post(f"{n['url']}/{n['topic']}", body.encode("utf-8"), h, opener)  # назва в тілі: заголовки лише ASCII
        out.append(("ntfy", ok, why))
    w = cfg["webhook"]
    if w["on"] and w["url"]:
        h = {"Content-Type": "application/json"}
        if w["token"]:
            h["Authorization"] = "Bearer " + w["token"]
        ok, why = _post(w["url"], json.dumps({"source": "meshpi", "title": title, "text": text, "level": level,
                                              "t": int(now or 0) or None}).encode("utf-8"), h, opener)
        out.append(("webhook", ok, why))
    return out


def send_async(cfg, title, text, level="info", log=None):
    def run():
        try:
            for ch, ok, why in send(cfg, title, text, level):
                if not ok and log:
                    log.warning("сповіщення %s не доставлено: %s", ch, why)
        except Exception:  # noqa: BLE001
            if log:
                log.exception("сповіщення")
    threading.Thread(target=run, daemon=True).start()
