import os, sys, tempfile, time, types
tmp = tempfile.mkdtemp()
os.environ["MESHPI_DB"] = os.path.join(tmp, "messages.db")
os.environ.pop("MESHPI_PASSWORD", None)
sys.path.insert(0, sys.argv[1])
import app as m

m.init_db(m.DB_PATH)
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print("ok  " if cond else "FAIL", name, extra)

ME, PEER, OTHER = 0x100, 0x2B2, 0x3C3
ID = lambda n: f"!{n:08x}"
now = int(time.time())


class Fake:
    def __init__(self):
        self.sent = []
        self.nodesByNum = {n: {"num": n, "user": {"id": ID(n), "longName": name}} for n, name in ((ME, "Me"), (PEER, "Peer"), (OTHER, "Other"))}
        self.nodes = {v["user"]["id"]: v for v in self.nodesByNum.values()}
        self.localNode = types.SimpleNamespace(nodeNum=ME)

    def getMyNodeInfo(self):
        return self.nodesByNum[ME]

    def sendData(self, data, destinationId=None, portNum=None, wantAck=False, channelIndex=0, **kw):
        self.sent.append(types.SimpleNamespace(w=data, dest=destinationId, port=portNum, ack=wantAck, ch=channelIndex))


fi = Fake()
m.use_iface(fi)
c = m.app.test_client()


def wp_packet(sender, wid, expire=now + 3600, lat=492000000, lon=314000000, name="Точка", lock=0, via=False, ch=0, icon=0x1F3E0):
    w = {"id": wid, "latitudeI": lat, "longitudeI": lon, "name": name, "description": "опис", "icon": icon}
    if expire:
        w["expire"] = expire
    if lock:
        w["lockedTo"] = lock
    return {"from": sender, "fromId": ID(sender), "channel": ch, "viaMqtt": via, "decoded": {"portnum": "WAYPOINT_APP", "waypoint": w}}


m.on_packet(wp_packet(PEER, 101), fi)
lst = c.get("/api/waypoints").get_json()
check("a received waypoint is stored and listed", len(lst) == 1 and lst[0]["name"] == "Точка" and abs(lst[0]["lat"] - 49.2) < 1e-6
      and lst[0]["icon"] == "🏠" and lst[0]["sender_name"] == "Peer" and not lst[0]["mine"] and lst[0]["expire"] > now)
m.on_packet(wp_packet(PEER, 101, name="Нова назва"), fi)
check("the same id updates the waypoint", [x["name"] for x in c.get("/api/waypoints").get_json()] == ["Нова назва"])
m.on_packet(wp_packet(PEER, 102, via=True), fi)
check("MQTT-relayed waypoints are ignored", len(c.get("/api/waypoints").get_json()) == 1)
m.on_packet(wp_packet(PEER, 103, expire=now - 10), fi)
check("an already expired waypoint is not stored", len(c.get("/api/waypoints").get_json()) == 1)
m.on_packet(wp_packet(PEER, 104, lat=2000000000), fi)
check("bad coordinates are ignored", len(c.get("/api/waypoints").get_json()) == 1)
m.on_packet(wp_packet(PEER, 105, lock=PEER), fi)
m.on_packet(wp_packet(OTHER, 105, name="Підміна"), fi)
check("a locked waypoint cannot be changed by another node",
      next(x for x in c.get("/api/waypoints").get_json() if x["id"] == 105)["name"] == "Точка")
m.on_packet(wp_packet(OTHER, 105, expire=0), fi)
check("nor deleted by another node", any(x["id"] == 105 for x in c.get("/api/waypoints").get_json()))
m.on_packet(wp_packet(PEER, 105, expire=0), fi)
check("its owner deletes it with expire = 0", not any(x["id"] == 105 for x in c.get("/api/waypoints").get_json()))
m.on_packet(wp_packet(PEER, 106, expire=2**31 - 1), fi)
check("a waypoint without expiry is listed with expire = null", next(x for x in c.get("/api/waypoints").get_json() if x["id"] == 106)["expire"] is None)
m.db.execute("UPDATE waypoint SET expire = ? WHERE id = 101", (now - 5,)); m.db.commit()
check("expired waypoints disappear from the list", not any(x["id"] == 101 for x in c.get("/api/waypoints").get_json()))

# ---- надсилання ----
r = c.post("/api/waypoints", json={"action": "send", "name": "Бази", "description": "збір", "icon": "🏕", "lat": 49.2, "lon": 31.4, "ttl": 3600, "lock": True, "ch": 2})
j = r.get_json()
s = fi.sent[-1]
check("send: Waypoint packet on the chosen channel, to everyone, with ack", r.status_code == 200 and s.dest == "^all" and s.ch == 2 and s.ack
      and s.w.name == "Бази" and s.w.icon == ord("🏕") and s.w.latitude_i == 492000000 and s.w.locked_to == ME and s.w.id == j["id"])
check("send: expiry is now + ttl", abs(s.w.expire - (now + 3600)) < 5)
mine = next(x for x in c.get("/api/waypoints").get_json() if x["id"] == j["id"])
check("send: stored as ours and locked", mine["mine"] and mine["locked"] and mine["icon"] == "🏕" and mine["ch"] == 2)
r = c.post("/api/waypoints", json={"action": "send", "id": j["id"], "name": "Бази 2", "lat": 49.3, "lon": 31.5, "ttl": 0})
check("send with an existing id edits it; ttl 0 means no expiry", r.status_code == 200 and fi.sent[-1].w.expire == 2**31 - 1
      and fi.sent[-1].w.id == j["id"] and next(x for x in c.get("/api/waypoints").get_json() if x["id"] == j["id"])["name"] == "Бази 2")
for bad, why in (({"name": ""}, "empty name"), ({"name": "x" * 31}, "name over 30 bytes"), ({"name": "x", "lat": 91}, "latitude"),
                 ({"name": "x", "ttl": 30}, "ttl below a minute"), ({"name": "x", "ch": 9}, "channel 9"), ({"name": "x", "description": "d" * 101}, "long description")):
    body = {"action": "send", "name": "x", "lat": 1, "lon": 1, "ttl": 0, "ch": 0, **bad}
    check(f"send rejected: {why}", c.post("/api/waypoints", json=body).status_code == 400)
check("unknown action rejected", c.post("/api/waypoints", json={"action": "x"}).status_code == 400)

# чужа заблокована точка: ні змінити, ні видалити
m.on_packet(wp_packet(PEER, 200, lock=PEER), fi)
n = len(fi.sent)
check("editing a waypoint locked to another node is refused", c.post("/api/waypoints", json={"action": "send", "id": 200, "name": "x", "lat": 1, "lon": 1}).status_code == 403
      and c.post("/api/waypoints", json={"action": "delete", "id": 200}).status_code == 403 and len(fi.sent) == n)
r = c.post("/api/waypoints", json={"action": "delete", "id": j["id"]})
check("delete our own: Waypoint with expire = 0 is sent and the row removed", r.status_code == 200 and fi.sent[-1].w.id == j["id"] and fi.sent[-1].w.expire == 0
      and not any(x["id"] == j["id"] for x in c.get("/api/waypoints").get_json()))
m.on_packet(wp_packet(PEER, 201), fi)
check("delete a free waypoint received from someone else", c.post("/api/waypoints", json={"action": "delete", "id": 201}).status_code == 200)

# ---- телеметрія ----
def tele_packet(sender, dm=None, pm=None, em=None, via=False):
    t = {}
    if dm is not None: t["deviceMetrics"] = dm
    if pm is not None: t["powerMetrics"] = pm
    if em is not None: t["environmentMetrics"] = em
    return {"from": sender, "fromId": ID(sender), "viaMqtt": via, "decoded": {"portnum": "TELEMETRY_APP", "telemetry": t}}

m.on_packet(tele_packet(PEER, dm={"batteryLevel": 87, "voltage": 4.05, "channelUtilization": 12.5, "airUtilTx": 1.2, "uptimeSeconds": 7200},
                         pm={"ch1Voltage": 5.1, "ch1Current": 410.0}, em={"temperature": 18.5}), fi)
m.on_packet(tele_packet(PEER, dm={"batteryLevel": 50}), fi)
m.on_packet(tele_packet(OTHER, dm={"batteryLevel": 400, "voltage": -3}), fi)
m.on_packet(tele_packet(OTHER, dm={"batteryLevel": 70}, via=True), fi)
rows = dict(m.db.execute("SELECT k, v FROM tele WHERE node = ?", (ID(PEER),)).fetchall())
check("device and power metrics are stored", rows == {"bat": 87.0, "volt": 4.05, "chan": 12.5, "air": 1.2, "up": 2.0, "p1v": 5.1, "p1c": 410.0}, str(rows))
check("points closer than the minimum gap are dropped", m.db.execute("SELECT COUNT(*) FROM tele WHERE node = ? AND k = 'bat'", (ID(PEER),)).fetchone()[0] == 1)
check("implausible values and MQTT packets are not stored", m.db.execute("SELECT COUNT(*) FROM tele WHERE node = ?", (ID(OTHER),)).fetchone()[0] == 0)

t0 = now - 3600 * 5
with m.db:
    for k in range(60):  # година за годиною: батарея падає, є й старіше за вікно
        m.db.execute("INSERT INTO tele (owner, node, t, k, v) VALUES ('main', ?, ?, 'bat', ?)", (ID(PEER), t0 + k * 300, 100 - k))
    m.db.execute("INSERT INTO tele (owner, node, t, k, v) VALUES ('main', ?, ?, 'bat', 1)", (ID(PEER), now - 3 * 86400))
    m.db.execute("INSERT INTO env (t, node, hops, k, v, owner) VALUES (?, ?, 1, 'temperature', 19.0, 'main')", (now - 600, ID(PEER)))
j = c.get(f"/api/telemetry?node={ID(PEER)}&hours=24").get_json()
by = {s["id"]: s for s in j["series"]}
check("telemetry API: device, power and environment series with labels and units",
      {"bat", "volt", "chan", "air", "up", "p1v", "p1c", "temperature"} <= set(by) and by["bat"]["unit"] == "%" and by["p1c"]["unit"] == "мА"
      and by["temperature"]["label"] == "Температура", str(sorted(by)))
check("telemetry API: window in hours (the 3-day-old point is outside 24 h)", len(by["bat"]["points"]) == 61 and by["bat"]["points"][0][1] > 1)
check("telemetry API: a wider window includes it", len(next(s for s in c.get(f"/api/telemetry?node={ID(PEER)}&hours=168").get_json()["series"] if s["id"] == "bat")["points"]) == 62)
check("telemetry API: series are ordered by time", all(a[0] <= b[0] for s in j["series"] for a, b in zip(s["points"], s["points"][1:])))
check("telemetry API: validation", c.get("/api/telemetry?node=x").status_code == 400 and c.get(f"/api/telemetry?node={ID(PEER)}&hours=q").status_code == 400)
check("telemetry API: unknown node gives no series", c.get(f"/api/telemetry?node={ID(OTHER)}").get_json()["series"] == [])
pts = [[k, float(k)] for k in range(1000)]
th = m.thin(pts, 100)
check("thin keeps at most the limit and the overall shape", len(th) <= 101 and th[0][1] < th[-1][1] and abs(th[-1][1] - 995) < 10, str((len(th), th[0], th[-1])))
m.db.execute("INSERT INTO metric (owner, t, chan, air, nodes) VALUES ('main', ?, 7.5, 0.9, 5)", (now - 100,)); m.db.commit()
own = {s["id"]: s for s in c.get(f"/api/telemetry?node={ID(ME)}").get_json()["series"]}
check("our own node gets channel utilisation from the panel's own samples", own["chan"]["points"][-1][1] == 7.5 and own["air"]["points"][-1][1] == 0.9)
sys.exit(0 if ok else 1)
