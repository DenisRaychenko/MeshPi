import sys
from pathlib import Path

root = Path(sys.argv[1])
app = root / "app.py"
s = app.read_text(encoding="utf8")


def sub(old, new):
    global s
    assert s.count(old) == 1, f"expected exactly one match for: {old[:60]!r} (found {s.count(old)})"
    s = s.replace(old, new)


# 1) table
sub('''    db.execute("CREATE TABLE IF NOT EXISTS probe (node TEXT, kind TEXT, t REAL, res TEXT)")  # див. save_probe
''', '''    db.execute("CREATE TABLE IF NOT EXISTS probe (node TEXT, kind TEXT, t REAL, res TEXT)")  # див. save_probe
    # показники довкілля від вузлів (телеметрія): одна точка на вузол і величину не частіше ENV_MIN_GAP
    db.execute("CREATE TABLE IF NOT EXISTS env (t INT, node TEXT, hops INT, k TEXT, v REAL)")
    db.execute("CREATE INDEX IF NOT EXISTS env_t ON env (t)")
''')

# 2) collection + aggregation, placed right before on_packet
sub('''def on_packet(packet, interface):
    if packet.get("rxRssi") is not None and sender_of(packet):
        rssi[sender_of(packet)] = packet["rxRssi"]
''', '''# ---- довкілля: усереднення показників вузлів у межах кількох стрибків ----
ENV_MAX_HOPS = int(os.environ.get("MESHPI_ENV_HOPS", "3"))     # враховуємо вузли не далі за стільки стрибків
ENV_WINDOW = 3 * 3600      # с: «поточними» вважаємо показники не старші за це
ENV_KEEP = 7 * 86400
ENV_MIN_GAP = 300          # с між записами однієї величини одного вузла
ENV_BUCKET = 1800          # с: крок історії для міні-графіка
# ключ у телеметрії -> (id, підпис, одиниця, допустимий діапазон; усе поза ним — збій датчика, відкидаємо)
ENV_METRICS = {
    "temperature": ("temperature", "Температура", "°C", (-60, 80)),
    "relativeHumidity": ("humidity", "Вологість", "%", (0, 100)),
    "barometricPressure": ("pressure", "Тиск", "гПа", (300, 1100)),
    "iaq": ("iaq", "Якість повітря (IAQ)", "", (0, 500)),
    "gasResistance": ("gas", "Опір газу", "МОм", (0, 100000)),
    "lux": ("lux", "Освітленість", "лк", (0, 200000)),
    "windSpeed": ("wind", "Вітер", "м/с", (0, 120)),
}
AIR_METRICS = {
    "pm25Standard": ("pm25", "PM2.5", "мкг/м³", (0, 1000)),
    "co2": ("co2", "CO₂", "ppm", (0, 10000)),
}
ENV_ORDER = ["temperature", "humidity", "pressure", "iaq", "gas", "lux", "wind", "pm25", "co2"]
ENV_INFO = {m[0]: m for m in [*ENV_METRICS.values(), *AIR_METRICS.values()]}
ENV_DIGITS = {"temperature": 1, "humidity": 0, "pressure": 1, "iaq": 0, "gas": 1, "lux": 0, "wind": 1,
              "pm25": 0, "co2": 0}


def record_env(packet):
    """Зберегти показники довкілля з пакета телеметрії (якщо це він і значення правдоподібні)."""
    dec = packet.get("decoded") or {}
    if dec.get("portnum") != "TELEMETRY_APP":
        return
    tel = dec.get("telemetry") or {}
    sender = sender_of(packet)
    if not sender:
        return
    hs = packet.get("hopStart")
    hops = hs - (packet.get("hopLimit") or 0) if hs else None
    vals = []
    for block, table in ((tel.get("environmentMetrics"), ENV_METRICS), (tel.get("airQualityMetrics"), AIR_METRICS)):
        for key, (mid, _label, _unit, (lo, hi)) in table.items():
            v = (block or {}).get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi:
                vals.append((mid, float(v)))
    if not vals:
        return
    now = int(time.time())
    try:
        with db_lock, db:
            for mid, v in vals:
                last = db.execute("SELECT MAX(t) FROM env WHERE node = ? AND k = ?", (sender, mid)).fetchone()[0]
                if last and now - last < ENV_MIN_GAP:
                    continue
                db.execute("INSERT INTO env VALUES (?,?,?,?,?)", (now, sender, hops, mid, v))
            db.execute("DELETE FROM env WHERE t < ?", (now - ENV_KEEP,))
    except sqlite3.Error:
        log.exception("не вдалося записати показники довкілля")


def env_summary():
    """Середнє за вузлами в межах ENV_MAX_HOPS стрибків: беремо останній показник кожного вузла (не старший
    за ENV_WINDOW), усереднюємо між вузлами; для міні-графіка — те саме по півгодинних відрізках за добу."""
    now = int(time.time())
    i = iface
    hops_now = {(n.get("user") or {}).get("id"): n.get("hopsAway") for n in all_nodes(i)}

    def hops_of(node, h):
        h = h if h is not None else hops_now.get(node)
        return h if h is not None and h <= ENV_MAX_HOPS else None

    with db_lock:
        recent = db.execute("SELECT node, k, v, t, hops FROM env WHERE t >= ? ORDER BY t",
                            (now - ENV_WINDOW,)).fetchall()
        day = db.execute("SELECT node, k, v, t, hops FROM env WHERE t >= ? ORDER BY t",
                         (now - 86400,)).fetchall()
    latest, sources = {}, {}
    for node, k, v, t, h in recent:
        latest[(node, k)] = (v, t, h)
    values: dict[str, list[float]] = {}
    for (node, k), (v, t, h) in latest.items():
        hh = hops_of(node, h)
        if hh is None:
            continue
        values.setdefault(k, []).append(v)
        src = sources.setdefault(node, {"id": node, "name": node_name(i, node), "hops": hh, "t": t, "values": {}})
        src["values"][k] = round(v, ENV_DIGITS.get(k, 1))
        src["t"] = max(src["t"], t)
    # історія: усереднення спочатку в межах вузла (щоб балакучий вузол не переважив інших), потім між вузлами
    buckets: dict[tuple[str, int], dict[str, list[float]]] = {}
    for node, k, v, t, h in day:
        if hops_of(node, h) is None:
            continue
        buckets.setdefault((k, t // ENV_BUCKET * ENV_BUCKET), {}).setdefault(node, []).append(v)
    series: dict[str, list[list[float]]] = {}
    for (k, b), per_node in sorted(buckets.items(), key=lambda kv: kv[0][1]):
        means = [sum(x) / len(x) for x in per_node.values()]
        series.setdefault(k, []).append([b + ENV_BUCKET // 2, round(sum(means) / len(means), ENV_DIGITS.get(k, 1))])
    metrics = []
    for k in ENV_ORDER:
        vs = values.get(k)
        if not vs:
            continue
        d = ENV_DIGITS.get(k, 1)
        mid, label, unit, _ = ENV_INFO[k]
        metrics.append({"id": mid, "label": label, "unit": unit, "avg": round(sum(vs) / len(vs), d),
                        "min": round(min(vs), d), "max": round(max(vs), d), "n": len(vs),
                        "series": series.get(k, [])})
    srcs = sorted(sources.values(), key=lambda x: (x["hops"], -x["t"]))
    for x in srcs:
        x["age"] = now - x.pop("t")
    return {"max_hops": ENV_MAX_HOPS, "window": ENV_WINDOW, "metrics": metrics, "sources": srcs, "now": now}


@app.get("/api/env")
def api_env():
    return jsonify(env_summary())


def on_packet(packet, interface):
    _quiet(lambda: record_env(packet))
    if packet.get("rxRssi") is not None and sender_of(packet):
        rssi[sender_of(packet)] = packet["rxRssi"]
''')
app.write_text(s, encoding="utf8")
print("patched app.py (env)")
