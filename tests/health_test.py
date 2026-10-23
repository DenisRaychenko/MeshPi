import os, sys, tempfile, time, types
tmp = tempfile.mkdtemp()
os.environ["MESHPI_DB"] = os.path.join(tmp, "messages.db")
os.environ["MESHPI_NODES_FILE"] = os.path.join(tmp, "nodes.json")
os.environ.pop("MESHPI_PASSWORD", None)
sys.path.insert(0, sys.argv[1])
import app as m
import extras as x

m.init_db(m.DB_PATH)
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print("ok  " if cond else "FAIL", name, extra)

T0 = 1_800_000_000
# ---- чиста логіка ----
check("deltas skip a counter reset", x.deltas([(0, 10), (1, 30), (2, 5), (3, 9)]) == [(1, 20), (3, 4)])
check("deltas of nothing", x.deltas([]) == [] and x.deltas([(0, 1)]) == [])


def series(rx, dup=0, bad=0, drop=0, rel=0, can=0, n=4, step=900):
    """Лічильники, що зростають рівномірно: за кожен інтервал rx, dup, bad…"""
    out = {k: [] for k in x.COUNTERS}
    for i in range(n):
        for key, per in (("ls_rx", rx), ("ls_dup", dup), ("ls_bad", bad), ("ls_drop", drop), ("ls_rel", rel), ("ls_can", can), ("ls_tx", 3)):
            out[key].append((T0 + i * step, per * i))
    return out


r = x.health_report({}, T0)
check("no data: only a note to wait", r["status"] == "info" and r["findings"][0]["id"] == "collect" and r["samples"] == 0)
r = x.health_report(series(100), T0 + 4000)
check("a quiet healthy mesh is ok", r["status"] == "ok" and r["findings"][-1]["id"] == "ok" and r["totals"]["rx"] == 300, str(r["findings"]))
r = x.health_report(series(100, dup=20), T0)
check("20% duplicates is a warning", r["status"] == "warn" and any(f["id"] == "dup" and f["level"] == "warn" for f in r["findings"]) and abs(r["ratios"]["dup"] - 0.2) < 1e-9)
r = x.health_report(series(100, dup=40), T0)
check("40% duplicates is critical", r["status"] == "crit")
s = series(100, bad=30)
r = x.health_report(s, T0)
check("many bad packets alone are not called interference (channel is quiet)", not any(f["id"] == "bad" for f in r["findings"]))
s["chan"] = [(T0, 10.0), (T0 + 2700, 40.0)]
r = x.health_report(s, T0)
check("many bad packets on a busy channel look like interference", any(f["id"] == "bad" for f in r["findings"]) and any(f["id"] == "chan" for f in r["findings"]))
s["chan"] = [(T0, 60.0)]
check("channel over 50% is critical", x.health_report(s, T0)["status"] == "crit")
r = x.health_report(series(100, drop=2), T0)
check("dropped packets are reported", any(f["id"] == "drop" for f in r["findings"]))
r = x.health_report(series(2), T0)
check("too few packets: no ratios, a note instead", r["ratios"]["dup"] is None and any(f["id"] == "few" for f in r["findings"]))
s = series(100, rel=40, can=30)
check("mostly cancelled relays are explained as normal", any(f["id"] == "cancel" and f["level"] == "info" for f in x.health_report(s, T0)["findings"]))
s = series(100)
s["ls_noise"] = [(T0, -115.0), (T0 + 900, -92.0)]
check("high noise floor is a warning", any(f["id"] == "noise" for f in x.health_report(s, T0)["findings"]))
check("per-interval series are built", len(x.health_report(series(100, dup=10, bad=5), T0)["series"]["dup"]) == 3)

# ---- запис пакета й API ----
ME = 0x100
ID = lambda n: f"!{n:08x}"


class Fake:
    def __init__(self):
        self.nodesByNum = {ME: {"num": ME, "user": {"id": ID(ME), "longName": "Me"}}}
        self.nodes = {ID(ME): self.nodesByNum[ME]}

    def getMyNodeInfo(self):
        return self.nodesByNum[ME]


fi = Fake()
m.use_iface(fi)
c = m.app.test_client()
check("API without data: waits for samples", c.get("/api/netstats").get_json()["findings"][0]["id"] == "collect")


def local_packet(i, rx, dup, bad=0, noise=-118):
    return {"from": ME, "fromId": ID(ME), "decoded": {"portnum": "TELEMETRY_APP", "telemetry": {"localStats": {
        "numPacketsTx": 5 * i, "numPacketsRx": rx, "numPacketsRxBad": bad, "numRxDupe": dup, "numTxRelay": 10 * i,
        "numTxRelayCanceled": i, "numTxDropped": 0, "noiseFloor": noise, "numOnlineNodes": 7, "uptimeSeconds": 1000 * i}}}}


now = int(time.time())
real_time = time.time
try:
    for i in range(4):  # чотири заміри з інтервалом 15 хв (години штучні, щоб пройшов ліміт частоти запису)
        time.time = lambda i=i: now - 3600 + i * 900 + 100
        m.record_tele(local_packet(i, 100 * i, 25 * i), owner="main")
finally:
    time.time = real_time
rows = m.db.execute("SELECT DISTINCT k FROM tele WHERE node = ?", (ID(ME),)).fetchall()
check("LocalStats are stored under ls_* keys", {"ls_rx", "ls_dup", "ls_bad", "ls_tx", "ls_rel", "ls_can", "ls_drop", "ls_noise", "ls_onl"} <= {r[0] for r in rows}, str(rows))
j = c.get("/api/netstats").get_json()
check("API: 25% duplicates over 4 samples is a warning", j["status"] == "warn" and abs(j["ratios"]["dup"] - 0.25) < 1e-9 and j["samples"] == 4, str(j["findings"]))
check("API: totals, hours and series present", j["totals"]["rx"] == 300 and j["hours"] == 24 and len(j["series"]["dup"]) == 3)
check("API: bad hours are refused", c.get("/api/netstats?hours=x").status_code == 400)
check("API: 6 hours window works", c.get("/api/netstats?hours=6").get_json()["hours"] == 6)
# чужі вузли й сміття не потрапляють у запис
before = m.db.execute("SELECT COUNT(*) FROM tele").fetchone()[0]
m.record_tele({"from": 0x200, "fromId": ID(0x200), "decoded": {"portnum": "TELEMETRY_APP", "telemetry": {"localStats": {"numPacketsRx": "x", "noiseFloor": float("nan")}}}}, owner="main")
check("garbage values are not stored", m.db.execute("SELECT COUNT(*) FROM tele").fetchone()[0] == before)
check("the page has the health section and script", "healthbox" in c.get("/").get_data(as_text=True) and c.get("/static/health.js").status_code == 200)
# жоден GET-маршрут не зареєстровано двічі (перший мовчки затінює другий: так було з /api/health панелі)
seen = {}
dups = []
for rule in m.app.url_map.iter_rules():
    for meth in rule.methods - {"HEAD", "OPTIONS"}:
        key = (rule.rule, meth)
        if key in seen:
            dups.append(key)
        seen[key] = rule.endpoint
check("no route is registered twice", not dups, str(dups))
check("the old /api/health of the overview page still answers", "connected" in c.get("/api/health").get_json())
sys.exit(0 if ok else 1)
