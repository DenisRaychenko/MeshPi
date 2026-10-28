import os, sys, tempfile, time
tmp = tempfile.mkdtemp()
os.environ["MESHPI_DB"] = os.path.join(tmp, "messages.db")
os.environ["MESHPI_NODES_FILE"] = os.path.join(tmp, "nodes.json")
os.environ.pop("MESHPI_PASSWORD", None)
sys.path.insert(0, sys.argv[1])
import app as m

m.init_db(m.DB_PATH)
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print("ok  " if cond else "FAIL", name, extra)

ME, RELAY, FAR, TWIN = 0x100, 0x2000A1, 0x300, 0x4000A1   # RELAY і TWIN мають однаковий останній байт 0xa1
ID = lambda n: f"!{n:08x}"
now = int(time.time())


class Fake:
    def __init__(self):
        mk = lambda n, name, hops: {"num": n, "user": {"id": ID(n), "longName": name}, "hopsAway": hops, "position": {"latitude": 50.0, "longitude": 30.0}}
        self.nodesByNum = {ME: mk(ME, "Me", 0), RELAY: mk(RELAY, "Relay", 1), FAR: mk(FAR, "Far", 3), TWIN: mk(TWIN, "Twin", 1)}
        self.nodes = {n["user"]["id"]: n for n in self.nodesByNum.values()}

    def getMyNodeInfo(self):
        return self.nodesByNum[ME]


m.use_iface(Fake())
c = m.app.test_client()


def packet(sender, to=0xFFFFFFFF, hop_start=3, hop_limit=3, relay=None, port="TEXT_MESSAGE_APP", payload=b"hello", t=None, mqtt=False):
    return {"from": sender, "fromId": ID(sender), "to": to, "toId": "^all" if to == 0xFFFFFFFF else ID(to), "id": sender, "rxTime": t or now,
            "rxSnr": 6.5, "rxRssi": -97, "hopStart": hop_start, "hopLimit": hop_limit, "relayNode": relay, "viaMqtt": mqtt,
            "decoded": {"portnum": port, "payload": payload}}


j = c.get("/api/live").get_json()
check("first request without parameters: only the current number, no history", j["packets"] == [] and j["seq"] == 0 and j["me"] == ID(ME), str(j))
i = m.cur_iface()
m.log_packet(packet(FAR, hop_start=3, hop_limit=1, relay=0xA1), "main", i)   # relayNode 0xa1: двоє кандидатів з однаковим байтом і однаковими стрибками
m.log_packet(packet(RELAY, hop_start=3, hop_limit=3, relay=0xA1), "main", i)                       # пряме чуття: ретранслятора немає
m.log_packet(packet(FAR, hop_start=3, hop_limit=2, relay=0x77), "main", i)                         # невідомий байт
j = c.get("/api/live?after=0").get_json()
p = j["packets"]
check("numbers grow by one and the stream goes from old to new", [x["n"] for x in p] == [1, 2, 3] and j["seq"] == 3, str([x["n"] for x in p]))
check("two equally close candidates for a relay byte give no via (a wrong line is worse than none)", p[0]["via"] is None, str(p[0]["via"]))
check("a packet heard directly has no via even if a relay byte is set", p[1]["via"] is None and p[1]["hops"] == 0)
check("an unknown relay byte gives no via", p[2]["via"] is None)
check("size is the payload length and names are filled in", p[0]["size"] == 5 and p[0]["from_name"] == "Far" and p[0]["to"] == "^all" and p[0]["to_name"] is None)
j = c.get("/api/live?after=2").get_json()
check("after=N returns only newer packets", [x["n"] for x in j["packets"]] == [3])
check("after the last number there is nothing new", c.get("/api/live?after=3").get_json()["packets"] == [])

# ретранслятор, якого можна визначити однозначно
del m.cur_iface().nodesByNum[TWIN]
m.log_packet(packet(FAR, hop_start=3, hop_limit=1, relay=0xA1), "main", i)
last = c.get("/api/live?after=3").get_json()["packets"][0]
check("a unique relay byte becomes the via node, with its name", last["via"] == ID(RELAY) and last["via_name"] == "Relay" and last["hops"] == 2, str(last))

# повтор записаного
m.log_packet(packet(FAR, port="TELEMETRY_APP", t=now - 7200), "main", i)
r = c.get("/api/live?since=600").get_json()["packets"]
check("since=T gives the last T seconds only (an old packet is left out)", len(r) == 4 and all(x["t"] >= now - 600 for x in r), str(len(r)))
check("limit=1 returns the newest of the window", c.get("/api/live?since=600&limit=1").get_json()["packets"][0]["n"] == 4)
for bad in ("after=x", "since=abc", "limit=y", "since=0", "since=999999"):
    check(f"bad parameter {bad} is refused", c.get("/api/live?" + bad).status_code == 400)

# MQTT-пакет теж у потоці (фільтр «з MQTT» — на сторінці), але позначений
m.log_packet(packet(FAR, mqtt=True), "main", i)
check("an MQTT packet is marked so the page can hide it", c.get("/api/live?after=5").get_json()["packets"][0]["mqtt"] is True)
check("the old packet log still works with the new fields", c.get("/api/packets?limit=3").status_code == 200 and "n" in c.get("/api/packets?limit=1").get_json()[0])

# роль «лише перегляд» бачить потік; жоден маршрут не продубльовано
check("the viewer role may read the stream", "/api/live" in m.VIEWER_GET)
seen, dups = {}, []
for rule in m.app.url_map.iter_rules():
    for meth in rule.methods - {"HEAD", "OPTIONS"}:
        if (rule.rule, meth) in seen:
            dups.append((rule.rule, meth))
        seen[(rule.rule, meth)] = rule.endpoint
check("no route is registered twice", not dups, str(dups))

page = c.get("/").get_data(as_text=True)
check("the page has the live mode toggle, ticker, drawer and the script, and the old schematic map is still there", all(k in page for k in ("mlive", "liveticker", "livedrawer", "liverep", "meshsvg")) and c.get("/static/live.js").status_code == 200)
check("the geographic live map is gone from the page", "livemap" not in page and "MESHPI_TILES" not in page)
mesh_js = c.get("/static/mesh.js").get_data(as_text=True)
check("mesh.js only exposes hooks for the live mode (the schematic itself is unchanged when it is off)", "meshLive.afterDraw" in mesh_js and "window.meshSchema" in mesh_js)
js = c.get("/static/live.js").get_data(as_text=True)
check("the script only polls the read-only stream (no POST, nothing is sent to the air)", "/api/live" in js and "method: 'POST'" not in js and "api('/api/send" not in js)
check("values from the mesh are inserted as text, not HTML", "innerHTML" not in js and "insertAdjacentHTML" not in js)
sys.exit(0 if ok else 1)
