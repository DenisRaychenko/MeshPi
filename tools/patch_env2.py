"""Second env patch: per-node environment values in /api/nodes (the node table column)."""
import sys
from pathlib import Path

root = Path(sys.argv[1])
app = root / "app.py"
s = app.read_text(encoding="utf8")


def sub(old, new):
    global s
    assert s.count(old) == 1, f"expected exactly one match for: {old[:60]!r} (found {s.count(old)})"
    s = s.replace(old, new)


sub('''@app.get("/api/env")
def api_env():
    return jsonify(env_summary())
''', '''@app.get("/api/env")
def api_env():
    return jsonify(env_summary())


def env_latest(nodes_list):
    """Останні показники довкілля кожного вузла за добу: {id: ({величина: значення}, час)}.
    З бази; якщо там порожньо, беремо те, що бібліотека запам'ятала з пакетів цього сеансу."""
    now = int(time.time())
    out: dict[str, tuple[dict, int]] = {}
    with db_lock:
        rows = db.execute("SELECT node, k, v, t FROM env WHERE t >= ? ORDER BY t", (now - 86400,)).fetchall()
    for node, k, v, t in rows:
        vals, last = out.get(node, ({}, 0))
        vals[k] = round(v, ENV_DIGITS.get(k, 1))
        out[node] = (vals, max(last, t))
    for n in nodes_list:
        nid = (n.get("user") or {}).get("id")
        if not nid or nid in out:
            continue
        vals = {}
        for block, table in ((n.get("environmentMetrics"), ENV_METRICS), (n.get("airQualityMetrics"), AIR_METRICS)):
            for key, (mid, _l, _u, (lo, hi)) in (table.items() if block else ()):
                v = block.get(key)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi:
                    vals[mid] = round(float(v), ENV_DIGITS.get(mid, 1))
        if vals:
            out[nid] = (vals, int(n.get("lastHeard") or now))
    return out
''')

sub('''    i = iface
    out = []
    for n in all_nodes(i):
        u, p = n.get("user", {}), n.get("position", {})
        nid = u.get("id") or (f"!{n['num']:08x}" if "num" in n else None)
        out.append({
            "id": nid, "long": u.get("longName"), "short": u.get("shortName"),
            "hw": u.get("hwModel"), "snr": n.get("snr"), "heard": n.get("lastHeard"),
            "hops": n.get("hopsAway"), "lat": p.get("latitude"), "lon": p.get("longitude"),
            "rssi": rssi.get(nid or ""),
        })''', '''    i = iface
    out = []
    all_n = all_nodes(i)
    env = env_latest(all_n)
    for n in all_n:
        u, p = n.get("user", {}), n.get("position", {})
        nid = u.get("id") or (f"!{n['num']:08x}" if "num" in n else None)
        e = env.get(nid or "")
        out.append({
            "id": nid, "long": u.get("longName"), "short": u.get("shortName"),
            "hw": u.get("hwModel"), "snr": n.get("snr"), "heard": n.get("lastHeard"),
            "hops": n.get("hopsAway"), "lat": p.get("latitude"), "lon": p.get("longitude"),
            "rssi": rssi.get(nid or ""),
            "env": e[0] if e else None, "env_t": e[1] if e else None,
            "mqtt": bool(n.get("viaMqtt")),  # вузол відомий через MQTT-міст, а не з радіоефіру
        })''')
app.write_text(s, encoding="utf8")
print("patched app.py (nodes env)")
