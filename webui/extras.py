"""Чиста логіка додаткових можливостей Meshpi (без Flask і без бібліотеки Meshtastic, тому легко тестується):
здоров'я мережі з LocalStats, перевірка безпеки, фільтри й CSV журналу пакетів, вимір дальності, автовідповідач,
прогноз сонця й профіль лінка. Маршрути й потоки, які це викликають, лежать в app.py."""
import base64
import binascii
import csv
import io
import math
import re
import time

# ---- здоров'я мережі (LocalStats) ----------------------------------------------------------------------------------------
# Вузол раз на ~15 хв повідомляє про себе накопичувальні лічильники (від запуску); різниця між двома замірами показує,
# що відбулося в ефірі за цей час. Пороги нижче це ЕВРИСТИКИ спільноти (https://github.com/Colorado-Mesh/mesh-client),
# а не правила прошивки: вони підказують, куди дивитися, і нічого не доводять.
LOCAL_KEYS = {  # поле LocalStats -> ключ у таблиці tele
    "numPacketsTx": "ls_tx", "numPacketsRx": "ls_rx", "numPacketsRxBad": "ls_bad", "numRxDupe": "ls_dup",
    "numTxRelay": "ls_rel", "numTxRelayCanceled": "ls_can", "numTxDropped": "ls_drop", "noiseFloor": "ls_noise",
    "numOnlineNodes": "ls_onl",
}
COUNTERS = ("ls_tx", "ls_rx", "ls_bad", "ls_dup", "ls_rel", "ls_can", "ls_drop")
DUP_WARN, DUP_CRIT = 0.15, 0.30      # частка дублікатів серед прийнятих пакетів
BAD_WARN = 0.20                      # частка пакетів із помилкою (разом із зайнятим ефіром це схоже на завади)
CHAN_WARN, CHAN_CRIT = 25.0, 50.0    # зайнятість каналу, %
NOISE_WARN = -100.0                  # дБм: типовий шумовий рівень LoRa набагато нижчий
MIN_RX = 20                          # менше пакетів за вікно: співвідношення нічого не варті


def deltas(points):
    """Приріст лічильника між сусідніми замірами [(t, v)]. Скидання (вузол перезапустився: значення впало) пропускаємо."""
    out = []
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        if v1 >= v0:
            out.append((t1, v1 - v0))
    return out


def health_report(series, now=None):
    """series: {ключ tele: [(t, v), ...]} за вікно. Підсумок за вікно, ряди співвідношень по інтервалах і висновки."""
    now = now or time.time()
    d = {k: deltas(series.get(k) or []) for k in COUNTERS}
    tot = {k: sum(v for _t, v in d[k]) for k in COUNTERS}
    samples = max((len(series.get(k) or []) for k in COUNTERS), default=0)

    def ratio(num, den):
        return (tot[num] / tot[den]) if tot[den] >= MIN_RX else None

    dup, bad = ratio("ls_dup", "ls_rx"), ratio("ls_bad", "ls_rx")
    by_t = {k: dict(d[k]) for k in COUNTERS}
    rows = {"dup": [], "bad": []}
    for t, rx in d["ls_rx"]:
        if rx >= 5:
            for name, key in (("dup", "ls_dup"), ("bad", "ls_bad")):
                if t in by_t[key]:
                    rows[name].append([t, by_t[key][t] / rx])
    chan_pts = series.get("chan") or []
    chan = chan_pts[-1][1] if chan_pts else None
    noise_pts = series.get("ls_noise") or []
    noise = noise_pts[-1][1] if noise_pts else None
    found = []
    if samples < 2:
        found.append({"level": "info", "id": "collect", "text": "Збираю дані: вузол надсилає статистику раз на 15 хв, потрібно "
                                                              "щонайменше два заміри."})
    else:
        if tot["ls_rx"] < MIN_RX:
            found.append({"level": "info", "id": "few", "text": f"За вікно прийнято лише {int(tot['ls_rx'])} пакетів: "
                                                                  "для висновків замало."})
        if dup is not None and dup > DUP_WARN:
            found.append({"level": "crit" if dup > DUP_CRIT else "warn", "id": "dup",
                          "text": f"Дублікатів {dup:.0%} прийнятого. Схоже, пакети ретранслюють зайві вузли (ROUTER, REPEATER "
                                  "або багато сусідів); перевірте ролі вузлів поруч."})
        if bad is not None and bad > BAD_WARN and (chan or 0) > CHAN_WARN:
            found.append({"level": "warn", "id": "bad",
                          "text": f"{bad:.0%} прийнятого з помилками, а канал зайнятий на {chan:.0f}%: схоже на завади "
                                  "(перевірте поруч імпульсні блоки живлення, DC-DC, Wi-Fi), а не на звичайні колізії."})
        if tot["ls_drop"] > 0:
            found.append({"level": "warn", "id": "drop", "text": f"{int(tot['ls_drop'])} пакетів відкинуто з черги передачі: "
                                                                   "вузол не встигає передавати (ефір зайнятий)."})
        if tot["ls_rel"] >= MIN_RX and tot["ls_can"] / tot["ls_rel"] > 0.5:
            found.append({"level": "info", "id": "cancel",
                          "text": "Більшість ретрансляцій скасовується: сусіди ретранслюють швидше. Це нормально для "
                                  "щільної мережі, ваш вузол там не потрібен."})
    if chan is not None and chan > CHAN_WARN:
        found.append({"level": "crit" if chan > CHAN_CRIT else "warn", "id": "chan",
                      "text": f"Канал зайнятий на {chan:.0f}%: понад {CHAN_WARN:.0f}% це вже тісно."})
    if noise is not None and noise > NOISE_WARN:
        found.append({"level": "warn", "id": "noise",
                      "text": f"Рівень шуму {noise:.0f} дБм вищий за {NOISE_WARN:.0f}: можливі завади."})
    if samples >= 2 and not any(f["level"] in ("warn", "crit") for f in found):
        found.append({"level": "ok", "id": "ok", "text": "Ознак перевантаження чи завад не видно."})
    worst = "crit" if any(f["level"] == "crit" for f in found) else "warn" if any(f["level"] == "warn" for f in found) else \
        "ok" if any(f["level"] == "ok" for f in found) else "info"
    return {"samples": samples, "status": worst, "totals": {k[3:]: int(v) for k, v in tot.items()},
            "ratios": {"dup": dup, "bad": bad}, "chan": chan, "noise": noise, "findings": found,
            "series": {"dup": rows["dup"], "bad": rows["bad"], "chan": chan_pts, "noise": noise_pts}}


# ---- тривоги за порогами (батарея, ефір, перезапуск, зв'язок із вузлом) ------------------------------------------------------
LINK_GAP = 120       # с без зв'язку з вузлом, після яких це подія
LINK_REPEAT = 600    # с: не частіше за раз на 10 хв про зв'язок (щоб мерехтіння не засипало сповіщеннями)


def alerts(state, snap, rules, now=None):
    """Один крок перевірки порогів. state — пам'ять між кроками (змінюється), snap — знімок:
    {online, me:{id,name,battery,uptime}, favs:[{id,name,battery}], chan:[останні виміри зайнятості, найновіший останній]}.
    rules: {battery, chan} (0 вимикає). Повертає [(вид, вузол, текст)]. Кожна тривога спрацьовує раз і знову лише після
    того, як показник повернувся в норму з запасом (гістерезис)."""
    now = now or time.time()
    out = []
    # зв'язок панелі з вузлом
    if not snap["online"]:
        state.setdefault("off_since", now)
        if now - state["off_since"] >= LINK_GAP and not state.get("link_lost") and now - state.get("link_t", 0) >= LINK_REPEAT:
            state.update(link_lost=True, link_t=now)
            out.append(("link_lost", (snap.get("me") or {}).get("id"), "Панель втратила зв'язок із вузлом"))
        return out          # без зв'язку решту не перевіряємо: знімок застарілий
    if state.get("link_lost"):
        out.append(("link_ok", (snap.get("me") or {}).get("id"),
                    f"Зв'язок із вузлом відновлено (не було {age_text(now - state['off_since'])})"))
        state["link_lost"], state["link_t"] = False, now
    state.pop("off_since", None)
    me = snap.get("me") or {}
    # перезапуск вузла: час роботи зменшився
    up = me.get("uptime")
    if isinstance(up, (int, float)):
        last = state.get("uptime")
        if last is not None and up + 60 < last:
            out.append(("reboot", me.get("id"), f"{me.get('name') or 'Вузол'} перезапустився (працював {age_text(last)})"))
        state["uptime"] = up
    # низький заряд свого вузла й обраних
    thr = rules.get("battery") or 0
    if thr:
        for n in [me, *snap.get("favs", [])]:
            lv, nid = n.get("battery"), n.get("id")
            if nid is None or not isinstance(lv, (int, float)) or lv > 100:  # 101: живлення від мережі
                continue
            key = "bat:" + nid
            if lv <= thr and not state.get(key):
                state[key] = True
                out.append(("bat_low", nid, f"🔋 {n.get('name') or nid}: заряд {lv:.0f}% (поріг {thr}%)"))
            elif lv >= thr + 10:
                state[key] = False
    # ефір стабільно перевантажений: три виміри поспіль вище порога
    thr = rules.get("chan") or 0
    chan = snap.get("chan") or []
    if thr and len(chan) >= 3:
        last3 = chan[-3:]
        if all(v >= thr for v in last3) and not state.get("chan"):
            state["chan"] = True
            out.append(("chan_high", me.get("id"), f"Ефір перевантажений: зайнятість каналу {last3[-1]:.0f}% (поріг {thr}%)"))
        elif all(v < thr - 5 for v in last3):
            state["chan"] = False
    return out


def age_text(sec):
    sec = max(int(sec), 0)
    return f"{sec // 60} хв" if sec < 3600 else f"{sec // 3600} год" if sec < 172800 else f"{sec // 86400} дн"


# ---- перевірка безпеки (лише читання) ----------------------------------------------------------------------------------------
# Прошивка 2.5.x–2.6.10 мала вразливість (CVE-2025-52464): однакові або слабкі ключі в різних пристроях; виправлено в 2.6.11.
# Перевіряємо те, що видно з даних вузла: повтори ключів, відомий ключ каналу за замовчуванням, відсутність шифрування,
# вихід у MQTT, застаріле адміністрування через канал. Секретні ключі панель не читає й не показує.
DEFAULT_PSK = ("AQ==", "01", "d4f1bb3a20290759f0bcffabcf4e6901")   # скорочений і розгорнутий ключ каналу за замовчуванням


def _hex(psk):
    if isinstance(psk, str):
        return psk
    return bytes(psk or b"").hex()


def is_default_psk(psk):
    h = _hex(psk)
    return h in ("01", "d4f1bb3a20290759f0bcffabcf4e6901") or h == "AQ=="


def security_audit(nodes, me_id, channels, cfg):
    """nodes: [{id, name, key (base64-рядок публічного ключа або ''), mqtt}]; channels: [{index, name, role, psk (bytes|hex),
    uplink, downlink, precision}]; cfg: {mqtt_on, ok_to_mqtt, admin_keys, admin_channel, managed}. -> {status, findings}"""
    f = []

    def add(level, ident, text, items=None):
        f.append({"level": level, "id": ident, "text": text, "items": items or []})

    keyed = [n for n in nodes if n.get("key")]
    groups: dict[str, list] = {}
    for n in keyed:
        groups.setdefault(n["key"], []).append(n)
    dups = [g for g in groups.values() if len(g) > 1]
    for g in dups:
        add("crit", "dupkey", f"Однаковий публічний ключ у {len(g)} вузлах: це клони або вада генерації ключів (CVE-2025-52464). "
            "Прямі повідомлення таким вузлам можна розшифрувати. Оновіть прошивку до 2.6.11+ і перегенеруйте ключі.",
            [n.get("name") or n["id"] for n in g])
    trivial = []
    for n in keyed:
        try:
            raw = base64.b64decode(n["key"], validate=True)
        except (binascii.Error, ValueError):
            continue
        if len(raw) == 32 and len(set(raw)) <= 2:
            trivial.append(n.get("name") or n["id"])
    if trivial:
        add("crit", "trivialkey", "Явно слабкий публічний ключ (майже всі байти однакові).", trivial)
    me = next((n for n in nodes if n["id"] == me_id), None)
    if me is not None and not me.get("key"):
        add("warn", "nokey", "У вашого вузла немає публічного ключа: прямі повідомлення не захищені (стара прошивка?).")
    for ch in channels:
        if ch.get("role") == "DISABLED":
            continue
        label = f"канал {ch['index']} «{ch.get('name') or 'без назви'}»"
        h = _hex(ch.get("psk"))
        if not h or h == "00":
            add("warn", f"nopsk{ch['index']}", f"{label.capitalize()} без шифрування: пакети читає будь-хто.")
        elif is_default_psk(ch.get("psk")):
            if ch["index"] == 0:
                add("info", "defpsk0", f"{label.capitalize()} має відомий усім ключ за замовчуванням: це відкритий канал, "
                    "його читає кожен, хто знає назву. Для приватної розмови заведіть окремий канал із власним ключем.")
            else:
                add("warn", f"defpsk{ch['index']}", f"{label.capitalize()} шифрується відомим усім ключем за замовчуванням: "
                    "це не захист. Згенеруйте випадковий ключ.")
        if ch.get("uplink") and cfg.get("mqtt_on"):
            add("warn" if is_default_psk(ch.get("psk")) else "info", f"uplink{ch['index']}",
                f"{label.capitalize()} передає пакети в MQTT" + (" із відомим ключем: їх прочитає будь-хто на брокері." if is_default_psk(ch.get("psk")) else "."))
        if ch["index"] == 0 and is_default_psk(ch.get("psk")) and (ch.get("precision") or 0) >= 32:
            add("info", "precision", "На відкритому каналі позицію вузла видно з повною точністю: розгляньте зменшення точності.")
    if cfg.get("ok_to_mqtt"):
        add("info", "okmqtt", "Увімкнено «дозволити пересилати мої пакети в MQTT» (config_ok_to_mqtt): інші вузли можуть публікувати ваш трафік.")
    if cfg.get("admin_channel") and not cfg.get("admin_keys"):
        add("warn", "legacyadmin", "Адміністрування через канал «admin» без ключів адміністратора: застарілий спосіб, "
            "кожен із ключем цього каналу може керувати вузлом. Задайте admin_key.")
    if not cfg.get("admin_keys") and not cfg.get("admin_channel"):
        add("info", "noadmin", "Ключів адміністратора немає: віддалене керування по радіо вимкнено (це безпечно).")
    if cfg.get("managed"):
        add("info", "managed", "Вузол у режимі «керований» (is_managed): налаштування змінюються лише віддалено.")
    worst = "crit" if any(x["level"] == "crit" for x in f) else "warn" if any(x["level"] == "warn" for x in f) else "ok"
    if not f:
        f.append({"level": "ok", "id": "ok", "text": "Тривожних ознак не знайдено.", "items": []})
    return {"status": worst, "findings": f, "nodes": len(nodes), "keyed": len(keyed)}


# ---- журнал пакетів: фільтри й CSV ---------------------------------------------------------------------------------------------
CSV_COLUMNS = ("t", "time", "from", "from_name", "to", "port", "snr", "rssi", "hops", "relay", "mqtt", "ch", "id")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def filter_packets(rows, args):
    """rows: словники журналу; args: порт, вузол (частина id чи назви), мінімальний/максимальний SNR, хопи, без MQTT, шифровані.
    Помилкове значення фільтра дає ValueError (маршрут відповідає 400), а не мовчки порожній результат."""
    port = (args.get("port") or "").strip()
    node = (args.get("node") or "").strip().lower()
    lo, hi, hops = args.get("snr_min"), args.get("snr_max"), args.get("hops")
    for name, v in (("snr_min", lo), ("snr_max", hi), ("hops", hops)):
        if v not in (None, "") and _num(v) is None:
            raise ValueError(f"{name}: число")
    lo, hi, hops = (_num(v) if v not in (None, "") else None for v in (lo, hi, hops))
    out = []
    for r in rows:
        if port and r.get("port") != port:
            continue
        if node and node not in str(r.get("from") or "").lower() and node not in str(r.get("from_name") or "").lower() \
                and node not in str(r.get("to") or "").lower():
            continue
        snr = r.get("snr")
        if lo is not None and (snr is None or snr < lo):
            continue
        if hi is not None and (snr is None or snr > hi):
            continue
        if hops is not None and r.get("hops") != int(hops):
            continue
        if args.get("nomqtt") in ("1", "true", True) and r.get("mqtt"):
            continue
        out.append(r)
    return out


def _safe(v):
    """Захист від CSV-ін'єкції: клітинка, що починається з = + - @ чи табуляції, у Excel стала б формулою."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") and not re.fullmatch(r"-?\d+(\.\d+)?", s) else s


def packets_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for r in rows:
        t = r.get("t")
        w.writerow([_safe(v) for v in (t, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else "", r.get("from"),
                                       r.get("from_name"), r.get("to"), r.get("port"), r.get("snr"), r.get("rssi"), r.get("hops"),
                                       r.get("relay"), int(bool(r.get("mqtt"))), r.get("ch"), r.get("id"))])
    return "\ufeff" + buf.getvalue()   # BOM: Excel відкриває UTF-8 без «кракозябрів»


# ---- вимір дальності (модуль Range Test) -------------------------------------------------------------------------------------
# Вузол-відправник шле «seq N» із заданим інтервалом; приймач (наш вузол) пише, хто, з яким SNR/RSSI і де він був.
# Відстань рахуємо від позиції нашого вузла до останньої відомої позиції відправника.
SEQ_RE = re.compile(rb"seq\s+(\d+)")
RANGE_GAP = 2 * 3600        # с: пауза довша за це — нова серія
RANGE_FIELDS = ("t", "node", "seq", "snr", "rssi", "hops", "lat", "lon", "dist")


def hops_used(packet):
    """Стрибки = hopStart − hopLimit; без hopStart (стара прошивка) невідомо."""
    hs = packet.get("hopStart")
    return hs - (packet.get("hopLimit") or 0) if hs else None


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(a)))


def range_seq(packet):
    """Номер пакета з RANGE_TEST_APP або None. Корисне навантаження може прийти як text чи як байти."""
    dec = packet.get("decoded") or {}
    if dec.get("portnum") != "RANGE_TEST_APP" or packet.get("viaMqtt"):
        return None
    pl = dec.get("payload")
    raw = pl if isinstance(pl, (bytes, bytearray)) else (dec.get("text") or "").encode("utf-8", "replace")
    m = SEQ_RE.search(bytes(raw))
    return int(m.group(1)) if m else None


def range_row(packet, seq, sender_pos, my_pos, now=None):
    """Рядок виміру. Без позиції відправника чи нашої відстань невідома (None), решта все одно корисна."""
    snr, rssi = packet.get("rxSnr"), packet.get("rxRssi")
    lat = lon = dist = None
    if sender_pos and sender_pos.get("latitude") is not None and sender_pos.get("longitude") is not None:
        lat, lon = float(sender_pos["latitude"]), float(sender_pos["longitude"])
        if my_pos and my_pos.get("latitude") is not None and my_pos.get("longitude") is not None:
            dist = haversine_km(float(my_pos["latitude"]), float(my_pos["longitude"]), lat, lon)
    return {"t": int(packet.get("rxTime") or now or time.time()), "node": packet.get("fromId") or (f"!{packet['from']:08x}" if isinstance(packet.get("from"), int) else None),
            "seq": seq, "snr": snr, "rssi": rssi, "hops": hops_used(packet), "lat": lat, "lon": lon, "dist": dist}


def range_summary(rows):
    """rows: рядки виміру одного підключення за вікно, за часом. Для кожного відправника: скільки пакетів дійшло, скільки
    пропущено за номерами (по сериях), найдальша відстань і межі SNR."""
    by: dict[str, list] = {}
    for r in sorted(rows, key=lambda r: r["t"]):
        by.setdefault(r["node"], []).append(r)
    out = []
    for node, rs in by.items():
        got = expected = 0
        series = []
        cur = None
        for r in rs:
            if cur is None or r["seq"] <= cur["last"] or r["t"] - cur["t"] > RANGE_GAP:
                cur = {"first": r["seq"], "last": r["seq"], "n": 0, "t": r["t"]}
                series.append(cur)
            cur["last"], cur["t"] = r["seq"], r["t"]
            cur["n"] += 1
        for sr in series:
            got += sr["n"]
            expected += sr["last"] - sr["first"] + 1
        d = [r["dist"] for r in rs if r["dist"] is not None]
        sn = [r["snr"] for r in rs if r["snr"] is not None]
        far = max((r for r in rs if r["dist"] is not None), key=lambda r: r["dist"], default=None)
        out.append({"node": node, "received": got, "expected": expected, "loss": (1 - got / expected) if expected else None,
                    "series": len(series), "max_km": max(d) if d else None, "snr_min": min(sn) if sn else None,
                    "snr_max": max(sn) if sn else None, "far_snr": far["snr"] if far else None, "last": rs[-1]["t"]})
    return sorted(out, key=lambda x: -x["last"])


def range_csv(rows, names=None):
    names = names or {}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["time", "node", "name", "seq", "snr", "rssi", "hops", "lat", "lon", "distance_km"])
    for r in sorted(rows, key=lambda r: r["t"]):
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["t"])), _safe(r["node"]), _safe(names.get(r["node"], "")), r["seq"],
                    r["snr"], r["rssi"], r["hops"], r["lat"], r["lon"], "" if r["dist"] is None else f"{r['dist']:.3f}"])
    return "﻿" + buf.getvalue()


# ---- автовідповідач і заплановані оголошення -----------------------------------------------------------------------------------
# Це передача в ефір без вашої участі, тому все вимкнено за замовчуванням і обмежено жорстко: пауза на вузол, ліміт на годину
# на все разом, не передавати, коли ефір зайнятий, не відповідати на давнє, на MQTT, на себе й на власні відповіді.
BOT_PREFIX = "[авто] "
AUTO_MAX_RULES, AUTO_MAX_ITEMS, AUTO_TEXT_BYTES = 10, 5, 120
WORD_RE = re.compile(r"^[\w.-]{1,20}$", re.U)
PLACEHOLDERS = ("name", "hops", "snr", "rssi", "nodes", "bat", "time", "temp", "hum", "press")


def auto_defaults():
    return {"reply": {"on": False, "scope": "dm", "cooldown": 300, "per_hour": 6, "chan_cutoff": 25,
                      "rules": [{"word": "info", "text": "Meshpi: почув вас за {hops} стрибків, SNR {snr}. Вузлів за годину: {nodes}."}]},
            "announce": {"on": False, "chan_cutoff": 25, "items": []}}


def _int(v, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v) or not lo <= v <= hi:
        raise ValueError
    return int(v)


def auto_update(cfg, body):
    """Нові налаштування -> (cfg, помилка). Усі межі перевіряються тут: інтерфейс лише зручність."""
    import copy
    new = copy.deepcopy(cfg)
    try:
        r = body.get("reply") if isinstance(body, dict) else None
        if isinstance(r, dict):
            if "on" in r:
                new["reply"]["on"] = bool(r["on"])
            if "scope" in r:
                if r["scope"] not in ("dm", "channel", "both"):
                    raise ValueError("scope: dm | channel | both")
                new["reply"]["scope"] = r["scope"]
            if "cooldown" in r:
                new["reply"]["cooldown"] = _int(r["cooldown"], 60, 86400)
            if "per_hour" in r:
                new["reply"]["per_hour"] = _int(r["per_hour"], 1, 30)
            if "chan_cutoff" in r:
                new["reply"]["chan_cutoff"] = _int(r["chan_cutoff"], 0, 100)
            if "rules" in r:
                if not isinstance(r["rules"], list) or len(r["rules"]) > AUTO_MAX_RULES:
                    raise ValueError(f"правил не більше {AUTO_MAX_RULES}")
                rules = []
                for x in r["rules"]:
                    word, text = str((x or {}).get("word", "")).strip().casefold(), str((x or {}).get("text", "")).strip()
                    if not WORD_RE.match(word):
                        raise ValueError("слово-тригер: 1–20 символів без пробілів")
                    if not text or len(text.encode("utf-8")) > AUTO_TEXT_BYTES:
                        raise ValueError(f"текст відповіді: до {AUTO_TEXT_BYTES} байт")
                    if word in [y["word"] for y in rules]:
                        raise ValueError(f"слово «{word}» повторюється")
                    rules.append({"word": word, "text": text})
                new["reply"]["rules"] = rules
        a = body.get("announce") if isinstance(body, dict) else None
        if isinstance(a, dict):
            if "on" in a:
                new["announce"]["on"] = bool(a["on"])
            if "chan_cutoff" in a:
                new["announce"]["chan_cutoff"] = _int(a["chan_cutoff"], 0, 100)
            if "items" in a:
                if not isinstance(a["items"], list) or len(a["items"]) > AUTO_MAX_ITEMS:
                    raise ValueError(f"оголошень не більше {AUTO_MAX_ITEMS}")
                items = []
                for k, x in enumerate(a["items"]):
                    x = x or {}
                    text = str(x.get("text", "")).strip()
                    if not text or len(text.encode("utf-8")) > AUTO_TEXT_BYTES:
                        raise ValueError(f"текст оголошення: до {AUTO_TEXT_BYTES} байт")
                    qf, qt = x.get("quiet_from"), x.get("quiet_to")
                    items.append({"id": _int(x.get("id", k + 1), 1, 999), "text": text, "every": _int(x.get("every", 360), 60, 10080),
                                  "ch": _int(x.get("ch", 0), 0, 7),
                                  "quiet_from": None if qf in (None, "") else _int(qf, 0, 23),
                                  "quiet_to": None if qt in (None, "") else _int(qt, 0, 23)})
                if len({i["id"] for i in items}) != len(items):
                    raise ValueError("id оголошень мають бути різними")
                new["announce"]["items"] = items
    except ValueError as e:
        return cfg, (str(e) or "некоректне значення: числа в межах, текст не порожній")
    return new, None


def auto_render(template, ctx):
    """Підставити {name}, {hops}… Лише відомі підстановки; невідомі лишаються як є, нічого не виконується."""
    def sub(m):
        if m.group(1) not in PLACEHOLDERS:
            return m.group(0)
        v = ctx.get(m.group(1))
        return "?" if v is None else str(v)
    out = re.sub(r"\{(\w+)\}", sub, template)
    while len(out.encode("utf-8")) > 200:       # ліміт Meshtastic; підстановки не мають його перевищити
        out = out[:-1]
    return out


def _budget_left(state, per_hour, now):
    sent = [t for t in state.get("sent", []) if now - t < 3600]
    state["sent"] = sent
    return per_hour - len(sent)


def auto_reply(cfg, state, msg, ctx, now=None):
    """msg: {text, sender, sender_name, dm (до нас особисто), mqtt, own, t, hops, snr, rssi}. ctx: {chan, local_ids, nodes…}.
    -> (текст відповіді чи None, причина відмови). state змінюється лише коли відповідь видається."""
    now = now or time.time()
    r = cfg["reply"]
    if not r["on"]:
        return None, "вимкнено"
    if msg.get("own") or msg.get("mqtt") or not msg.get("sender"):
        return None, "не для відповіді"
    if msg["sender"] in (ctx.get("local_ids") or ()):
        return None, "вузол цієї ж панелі"
    if msg["text"].startswith(BOT_PREFIX.strip()):
        return None, "це відповідь автовідповідача"
    if now - (msg.get("t") or now) > 120:
        return None, "повідомлення застаріло"
    if not (r["scope"] == "both" or (r["scope"] == "dm" and msg.get("dm")) or (r["scope"] == "channel" and not msg.get("dm"))):
        return None, "інша область"
    word = (msg["text"].strip().split() or [""])[0].strip("!/.,?").casefold()
    rule = next((x for x in r["rules"] if x["word"] == word), None)
    if not rule:
        return None, "немає правила"
    if now - state.setdefault("last", {}).get(msg["sender"], 0) < r["cooldown"]:
        return None, "пауза для цього вузла"
    if r["chan_cutoff"] and (ctx.get("chan") or 0) >= r["chan_cutoff"]:
        return None, "ефір зайнятий"
    if _budget_left(state, r["per_hour"], now) <= 0:
        return None, "ліміт на годину вичерпано"
    state["last"][msg["sender"]] = now
    state.setdefault("sent", []).append(now)
    if len(state["last"]) > 500:
        state["last"] = {k: v for k, v in state["last"].items() if now - v < 86400}
    values = {"name": msg.get("sender_name"), "hops": msg.get("hops"), "snr": None if msg.get("snr") is None else f"{msg['snr']:.0f}",
              "rssi": msg.get("rssi"), **{k: ctx.get(k) for k in ("nodes", "bat", "time", "temp", "hum", "press")}}
    return BOT_PREFIX + auto_render(rule["text"], values), "ok"


def in_quiet(h, qf, qt):
    """Година h у «тихих годинах» [qf, qt) (може перетинати північ); без меж — завжди можна."""
    if qf is None or qt is None or qf == qt:
        return False
    return qf <= h < qt if qf < qt else (h >= qf or h < qt)


def auto_due(cfg, state, ctx, now=None):
    """Оголошення, які пора передати зараз -> [(елемент, текст)]. Не більше одного за крок (ефір спільний)."""
    now = now or time.time()
    a = cfg["announce"]
    if not a["on"] or not a["items"]:
        return []
    if a["chan_cutoff"] and (ctx.get("chan") or 0) >= a["chan_cutoff"]:
        return []
    hour = time.localtime(now).tm_hour
    last = state.setdefault("ann", {})
    for it in a["items"]:
        key = str(it["id"])
        if key not in last:      # перший запуск: відлік від зараз, а не негайна передача
            last[key] = now
            continue
        if now - last[key] < it["every"] * 60 or in_quiet(hour, it["quiet_from"], it["quiet_to"]):
            continue
        if _budget_left(state, ctx.get("per_hour", 6), now) <= 0:
            return []
        last[key] = now
        state.setdefault("sent", []).append(now)
        return [(it, BOT_PREFIX + auto_render(it["text"], {k: ctx.get(k) for k in ("nodes", "bat", "time", "temp", "hum", "press")}))]
    return []


# ---- планувальник лінка: профіль висот, зона Френеля, бюджет лінка -----------------------------------------------------------
# Висоти беруться із зовнішньої служби лише за вашою командою (див. app.py), тут тільки математика. Модель наближена:
# вільний простір плюс перевірка прямої видимості з кривизною Землі (k = 4/3) і першою зоною Френеля. Дифракцію, дерева й
# будівлі вона не знає, тому це підказка для вибору місця, а не гарантія; перевірте виміром дальності.
EARTH_KM = 6371.0088
K_FACTOR = 4 / 3
FRESNEL_OK = 0.6        # прийнято вважати: вільні 60% першої зони достатньо для майже вільного простору


def sample_points(lat1, lon1, lat2, lon2, n):
    """n рівновіддалених точок між A і B (лінійно за широтою й довготою: на десятки км похибка мізерна)."""
    return [(lat1 + (lat2 - lat1) * k / (n - 1), lon1 + (lon2 - lon1) * k / (n - 1)) for k in range(n)]


def link_profile(elev, dist_km, h1, h2, freq_mhz):
    """elev: висоти рельєфу вздовж лінка (м, перша й остання точка — кінці), h1/h2: висота антен над землею (м)."""
    n = len(elev)
    if n < 3 or dist_km <= 0 or freq_mhz <= 0:
        raise ValueError("профіль: потрібні щонайменше 3 точки, відстань і частота")
    ha, hb = elev[0] + h1, elev[-1] + h2
    pts = []
    for idx, e in enumerate(elev):
        d1 = dist_km * idx / (n - 1)
        d2 = dist_km - d1
        bulge = d1 * d2 / (2 * K_FACTOR * EARTH_KM) * 1000
        los = ha + (hb - ha) * idx / (n - 1)
        f1 = 17.32 * math.sqrt(d1 * d2 / ((freq_mhz / 1000) * dist_km)) if 0 < idx < n - 1 else 0.0
        clear = los - (e + bulge)
        pts.append({"d": round(d1, 3), "terrain": round(e, 1), "bulge": round(bulge, 1), "los": round(los, 1), "f1": round(f1, 1),
                    "clear": round(clear, 1), "ratio": round(clear / f1, 2) if f1 > 0 else None})
    inner = [p for p in pts[1:-1]]
    worst = min(inner, key=lambda p: p["clear"] if p["ratio"] is None else p["ratio"] * max(p["f1"], 1e-9))
    min_clear = min(p["clear"] for p in inner)
    ratios = [p["ratio"] for p in inner if p["ratio"] is not None]
    min_ratio = min(ratios) if ratios else None
    verdict = "blocked" if min_clear < 0 else "marginal" if (min_ratio is not None and min_ratio < FRESNEL_OK) else "clear"
    return {"points": pts, "verdict": verdict, "min_clear": round(min_clear, 1), "min_ratio": min_ratio,
            "worst_d": worst["d"], "dist_km": round(dist_km, 3)}


def link_budget(dist_km, freq_mhz, tx_dbm, gain_tx, gain_rx, bw_khz, snr_limit, nf_db=6.0, loss_db=0.0):
    """Бюджет у вільному просторі. Чутливість = теплова шумова підлога (−174 дБм/Гц) + смуга + коефіцієнт шуму + SNR, потрібний
    пресету (LongFast ≈ −17,5 дБ). Значення прийомного кінця орієнтовні (NF приймача залежить від плати)."""
    fspl = 32.44 + 20 * math.log10(freq_mhz) + 20 * math.log10(max(dist_km, 1e-3))
    prx = tx_dbm + gain_tx + gain_rx - fspl - loss_db
    sens = -174 + 10 * math.log10(bw_khz * 1000) + nf_db + snr_limit
    return {"fspl": round(fspl, 1), "prx": round(prx, 1), "sensitivity": round(sens, 1), "margin": round(prx - sens, 1)}


# ---- сонячний вузол: прогноз виробітку й запас акумулятора ---------------------------------------------------------------------
def solar_defaults():
    return {"on": False, "lat": None, "lon": None, "dec": 35, "az": 0, "kwp": 0.1, "cap_wh": 0, "load_w": 0.3, "eff": 0.75}


def solar_update(cfg, body):
    new = dict(cfg)
    try:
        if not isinstance(body, dict):
            raise ValueError("очікується об'єкт")

        def num(key, lo, hi, integer=False):
            v = body[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
                raise ValueError(f"{key}: число від {lo} до {hi}")
            new[key] = int(v) if integer else float(v)
        if "on" in body:
            new["on"] = bool(body["on"])
        for key, lo, hi, integer in (("lat", -90, 90, False), ("lon", -180, 180, False), ("dec", 0, 90, True), ("az", -180, 180, True),
                                     ("kwp", 0.01, 100, False), ("cap_wh", 0, 100000, False), ("load_w", 0.01, 100, False), ("eff", 0.3, 1, False)):
            if key in body and body[key] is not None:
                num(key, lo, hi, integer)
        if new["on"] and (new["lat"] is None or new["lon"] is None):
            raise ValueError("потрібні координати панелі (lat, lon)")
    except ValueError as e:
        return cfg, str(e)
    return new, None


def parse_forecast(js):
    """Відповідь forecast.solar -> (точки [(epoch, W)], Вт·год за добу {YYYY-MM-DD: Wh}, лишилось запитів на годину).
    Час у відповіді місцевий для панелі; зсув беремо з message.info (time і time_utc)."""
    import datetime as dt
    res, msg = js.get("result") or {}, js.get("message") or {}
    info = msg.get("info") or {}
    try:
        offset = dt.datetime.fromisoformat(info["time"]).utcoffset() or dt.timedelta(0)
    except (KeyError, ValueError, TypeError):
        offset = dt.timedelta(0)
    pts = []
    for k, w in (res.get("watts") or {}).items():
        try:
            t = dt.datetime.strptime(k, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone(offset)).timestamp()
        except ValueError:
            continue
        if isinstance(w, (int, float)) and w >= 0:
            pts.append((t, float(w)))
    pts.sort()
    days = {k: float(v) for k, v in (res.get("watt_hours_day") or {}).items() if isinstance(v, (int, float))}
    return pts, days, (msg.get("ratelimit") or {}).get("remaining")


def solar_projection(points, cfg, soc_pct, now):
    """Крок 15 хв від now до кінця прогнозу: виробіток (ККД eff) мінус споживання. points — (час кінця періоду, середня потужність Вт).
    Якщо ємність невідома (cap_wh = 0), повертає лише добовий баланс. -> словник для сторінки."""
    if not points:
        return {"ok": False}
    end = points[-1][0]
    step = 900
    gen_wh = load_wh = 0.0
    cap = cfg["cap_wh"]
    soc = cap * (soc_pct if soc_pct is not None else 100) / 100.0
    low, low_t, empty_t = soc, now, None
    t = now
    while t < end:
        dt_h = step / 3600
        # потужність періоду, що закінчується в te, діє на інтервал (te−період, te]; період оцінюємо за відстанню до сусідніх точок
        g = 0.0
        for k, (te, w) in enumerate(points):
            prev = points[k - 1][0] if k else te - 3600
            if prev < t + step / 2 <= te:
                g = w
                break
        gen = g * cfg["eff"] * dt_h
        use = cfg["load_w"] * dt_h
        gen_wh += gen
        load_wh += use
        if cap:
            soc = min(cap, max(0.0, soc + gen - use))
            if soc < low:
                low, low_t = soc, t + step
            if soc <= 0 and empty_t is None:
                empty_t = t + step
        t += step
    out = {"ok": True, "gen_wh": round(gen_wh, 1), "load_wh": round(load_wh, 1), "balance_wh": round(gen_wh - load_wh, 1), "until": end}
    if cap:
        out.update(min_pct=round(100 * low / cap, 1), min_at=low_t, empty_at=empty_t, risk=empty_t is not None,
                   end_pct=round(100 * soc / cap, 1))
    return out
