import os, sys, tempfile, time
tmp = tempfile.mkdtemp()
os.environ["MESHPI_DB"] = os.path.join(tmp, "messages.db")
os.environ["MESHPI_NODES_FILE"] = os.path.join(tmp, "nodes.json")
os.environ["MESHPI_GEO_FILE"] = os.path.join(tmp, "geofence.json")
os.environ.pop("MESHPI_PASSWORD", None)
sys.path.insert(0, sys.argv[1])
import app as m
import extras as x

m.init_db(m.DB_PATH)
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print("ok  " if cond else "FAIL", name, extra)

T = 1_800_000_000

# ---- геозони ----
cfg, why = x.geo_update(x.geo_defaults(), {"on": True, "zones": [{"name": "Дім", "lat": 50.0, "lon": 30.0, "radius_m": 500}]})
check("a zone is accepted and fills the defaults", why is None and cfg["zones"][0]["who"] == "all" and cfg["zones"][0]["enter"] and cfg["zones"][0]["id"] == 1, str(why))
for bad in ({"name": "", "lat": 50, "lon": 30, "radius_m": 500}, {"name": "a", "lat": 91, "lon": 30, "radius_m": 500},
            {"name": "a", "lat": 0, "lon": 0, "radius_m": 500}, {"name": "a", "lat": 50, "lon": 30, "radius_m": 10},
            {"name": "a", "lat": 50, "lon": 30, "radius_m": 500, "who": "x"}, {"name": "a", "lat": "50", "lon": 30, "radius_m": 500},
            {"name": "a", "lat": True, "lon": 30, "radius_m": 500}):
    check(f"bad zone {str(bad)[:40]} is refused", x.geo_update(x.geo_defaults(), {"zones": [bad]})[1])
check("too many zones are refused", x.geo_update(x.geo_defaults(), {"zones": [{"name": "a", "lat": 50, "lon": 30, "radius_m": 500, "id": k} for k in range(11)]})[1])
check("duplicate zone ids are refused", x.geo_update(x.geo_defaults(), {"zones": [{"name": "a", "lat": 50, "lon": 30, "radius_m": 500, "id": 1}] * 2})[1])

state = {}
inside = {"id": "!1", "name": "A", "lat": 50.0, "lon": 30.0, "fav": False, "heard": T}
far = {**inside, "lat": 50.1}
check("the first sighting only remembers the node (no event flood at start)", x.geo_step(cfg, state, [inside], T) == [] and state["1|!1"] is True)
ev = x.geo_step(cfg, state, [far], T + 60)
check("leaving the circle gives one geo_out event", [e[0] for e in ev] == ["geo_out"] and "Дім" in ev[0][2], str(ev))
check("staying outside gives nothing more", x.geo_step(cfg, state, [far], T + 120) == [])
ev = x.geo_step(cfg, state, [inside], T + 180)
check("coming back gives one geo_in event", [e[0] for e in ev] == ["geo_in"], str(ev))
edge = {**inside, "lat": 50.0 + 0.0048}   # ≈ 534 м: за радіусом 500 м, але в межах гістерезису 1.1
check("a node sitting on the edge does not flicker (hysteresis)", x.geo_step(cfg, state, [edge], T + 200) == [] and state["1|!1"] is True)
check("a node not heard for hours is ignored", x.geo_step(cfg, {}, [{**far, "heard": T - 5 * 3600}], T) == [])
check("a (0,0) position is ignored", x.geo_step(cfg, {}, [{**inside, "lat": 0.0, "lon": 0.0}], T) == [])
only_fav = x.geo_update(cfg, {"zones": [{**cfg["zones"][0], "who": "fav"}]})[0]
check("who=fav skips ordinary nodes", x.geo_step(only_fav, {}, [inside], T) == [] and "1|!1" not in {})
off = x.geo_update(cfg, {"on": False})[0]
check("a disabled geofence does nothing", x.geo_step(off, {}, [inside], T) == [])
no_exit = x.geo_update(cfg, {"zones": [{**cfg["zones"][0], "exit": False}]})[0]
st2 = {}
x.geo_step(no_exit, st2, [inside], T)
check("exit events can be switched off", x.geo_step(no_exit, st2, [far], T + 60) == [])

# ---- проблеми мережі ----
N = lambda **kw: {"id": "!a", "name": "A", "role": "CLIENT", "hops": 1, "snr": 5, "heard": T - 60, "lat": 50.0, "lon": 30.0, "air_tx": 1, "chan_util": 5,
                  "bat": 80, "uptime": 99999, "fav": False, "mqtt": False, **kw}
kinds = lambda rows, **kw: [i["kind"] for i in x.mesh_issues(rows, "!me", T, **kw)]
check("a healthy network has no issues", kinds([N()]) == [])
check("a node that uses 10%+ of airtime is flagged", kinds([N(air_tx=14)]) == ["airtime"])
check("low battery matters for favourites and our own node, not for strangers", kinds([N(bat=10, fav=True)]) == ["battery"] and kinds([N(bat=10)]) == []
      and kinds([N(id="!me", bat=3)]) == ["battery"] and x.mesh_issues([N(id="!me", bat=3)], "!me", T)[0]["level"] == "crit")
check("a fresh reboot is noticed", kinds([N(uptime=120)]) == ["reboot"])
check("(0,0) coordinates are flagged", kinds([N(lat=0.0, lon=0.0)]) == ["badpos"])
check("a router with no position is flagged", kinds([N(role="ROUTER", lat=None, lon=None)]) == ["router_nopos"])
check("a weak direct link is noted", kinds([N(hops=0, snr=-17)]) == ["weak"])
check("three routers close by are noted", "routers" in kinds([N(id=f"!r{k}", name=f"R{k}", role="ROUTER", hops=1) for k in range(3)]))
check("duplicate short names are noted", "dupname" in kinds([N(id="!1"), N(id="!2")]))
check("nodes not heard for 6 hours and MQTT nodes are left alone", kinds([N(air_tx=50, heard=T - 7 * 3600), N(id="!b", name="B", air_tx=50, mqtt=True)]) == [])
check("a relay carrying most packets is a single point of failure", kinds([N()], relays=[("!r", "R", 80), ("!s", "S", 10)]) == ["relay"]
      and kinds([N()], relays=[("!r", "R", 15), ("!s", "S", 3)]) == [] and kinds([N()], relays=[("!r", "R", 100)]) == [])
check("a busy channel is a warning", kinds([N()], chan=55) == ["chan"] and kinds([N()], chan=20) == [])
mix = x.mesh_issues([N(air_tx=20), N(id="!b", name="B", bat=2, fav=True)], "!me", T)
check("the most serious issues come first", [i["level"] for i in mix] == ["crit", "warn"])

# ---- покриття ----
nodes = [{"id": "!n", "name": "North", "lat": 50.05, "lon": 30.0, "hops": 0, "snr": 7, "heard": T},          # ≈5.6 км на північ
         {"id": "!e", "name": "East", "lat": 50.0, "lon": 30.2, "hops": 2, "snr": None, "heard": T},          # ≈14 км на схід
         {"id": "!e2", "name": "East2", "lat": 50.0, "lon": 30.03, "hops": 0, "snr": 3, "heard": T},          # ≈2 км на схід
         {"id": "!old", "name": "Old", "lat": 50.0, "lon": 29.9, "hops": 0, "snr": 1, "heard": T - 40 * 86400},
         {"id": "!nopos", "name": "NoPos", "lat": None, "lon": None, "hops": 0, "heard": T},
         {"id": "!mq", "name": "Mqtt", "lat": 51, "lon": 31, "hops": 3, "mqtt": True, "heard": T},
         {"id": "!zero", "name": "Zero", "lat": 0.0, "lon": 0.0, "hops": 0, "heard": T}]
r = x.coverage(50.0, 30.0, nodes, 12, T, 7 * 86400)
check("only nodes with a real position, heard in the window, not via MQTT are counted", r["n"] == 3, str(r["n"]))
check("the farthest node and the farthest direct one", r["farthest"]["name"] == "East" and 13 < r["farthest"]["km"] < 15 and r["direct_farthest"]["name"] == "North", str(r["farthest"]))
sec = {s["center"]: s for s in r["sectors"]}
check("north falls into sector 0, east into sector 90", sec[0]["count"] == 1 and sec[0]["direct"] == 1 and sec[0]["snr"] == 7 and sec[90]["count"] == 2 and sec[90]["direct"] == 1)
check("distance bands add up", [b["count"] for b in r["bands"]] == [0, 1, 1, 1, 0, 0] and sum(b["count"] for b in r["bands"]) == 3, str(r["bands"]))
check("with no window old nodes count too", x.coverage(50.0, 30.0, nodes, 12, T)["n"] == 4)
check("an empty network gives an empty report", x.coverage(50.0, 30.0, [], 12, T)["farthest"] is None)
check("bearings: N, E, S, W", [round(x.bearing_deg(50, 30, *p)) for p in ((51, 30), (50, 31), (49, 30), (50, 29))] == [0, 90, 180, 270])

# ---- літак, пошук ----
check("aircraft: height over the threshold only", x.aircraft(9000) and not x.aircraft(300) and not x.aircraft(None) and not x.aircraft(True) and x.aircraft(1500, 1500))
check("LIKE wildcards are escaped", x.like_escape("50%_a\\b") == "50\\%\\_a\\\\b")


class Fake:
    def __init__(self):
        mk = lambda n, name, hops, **kw: {"num": n, "user": {"id": f"!{n:08x}", "longName": name, "shortName": name[:4]}, "hopsAway": hops, "lastHeard": int(time.time()) - 30, **kw}
        self.nodesByNum = {0x100: mk(0x100, "Me", 0, position={"latitude": 50.0, "longitude": 30.0}, deviceMetrics={"batteryLevel": 90, "uptimeSeconds": 5000}),
                           0x200: mk(0x200, "Plane", 3, position={"latitude": 50.1, "longitude": 30.1, "altitude": 9000}, snr=-3),
                           0x300: mk(0x300, "Hill", 1, position={"latitude": 50.0, "longitude": 30.2, "altitude": 300}, snr=6,
                                     deviceMetrics={"batteryLevel": 55, "voltage": 3.9, "airUtilTx": 12.5, "channelUtilization": 8}, isFavorite=True)}
        self.nodes = {n["user"]["id"]: n for n in self.nodesByNum.values()}

    def getMyNodeInfo(self):
        return self.nodesByNum[0x100]


m.use_iface(Fake())
c = m.app.test_client()
nodes_json = {n["id"]: n for n in c.get("/api/nodes").get_json()}
check("/api/nodes marks the high node as a likely aircraft and not the hill", nodes_json["!00000200"]["aircraft"] is True and nodes_json["!00000300"]["aircraft"] is False
      and nodes_json["!00000200"]["alt"] == 9000)
j = c.get("/api/latest").get_json()
rows = {n["id"]: n for n in j["nodes"]}
check("/api/latest has one row per node, favourites first, with device metrics", len(rows) == 3 and j["nodes"][0]["id"] == "!00000300" and rows["!00000300"]["bat"] == 55
      and rows["!00000300"]["air"] == 12.5 and rows["!00000100"]["me"] and rows["!00000200"]["aircraft"])
j = c.get("/api/issues").get_json()["issues"]
check("/api/issues reports the node using a lot of airtime", any(i["kind"] == "airtime" and "Hill" in i["text"] for i in j), str(j))
j = c.get("/api/coverage").get_json()
check("/api/coverage uses our own position from the node", j["center"] == {"lat": 50.0, "lon": 30.0} and j["report"]["n"] == 2 and j["report"]["farthest"]["name"] == "Hill", str(j)[:200])
check("coverage hours must be a number", c.get("/api/coverage?hours=x").status_code == 400)

# пошук
for t, txt in enumerate(("Привіт, мережо", "ціна 50% знижка", "змінна_x", "ping")):
    m.add_msg("!00000200", "Plane", "^all", 0, txt, 5.0, 0, 1000 + t, None, 0, owner=m.cur_id())
m.add_msg("!00000200", "Plane", "^all", 0, "👍 класно", 5.0, 0, 2000, 1000, 1, owner=m.cur_id())
res = lambda q: [r["text"] for r in c.get("/api/messages/search?q=" + q).get_json()]
check("search ignores case and finds substrings", res("ПРИВІТ") == ["Привіт, мережо"], str(res("ПРИВІТ")))
check("search treats % and _ literally", res("50%25") == ["ціна 50% знижка"] and res("_x") == ["змінна_x"] and res("%25%25") == [])
check("reactions are not search results", res("класно") == [])
check("search needs 2..80 characters", c.get("/api/messages/search?q=a").status_code == 400 and c.get("/api/messages/search?q=" + "я" * 81).status_code == 400
      and c.get("/api/messages/search").status_code == 400 and c.get("/api/messages/search?q=ab&limit=x").status_code == 400)
m.add_msg("!00000200", "Plane", "^all", 0, "zzz перше", 5.0, 0, 3000, None, 0, owner=m.cur_id())
m.add_msg("!00000200", "Plane", "^all", 0, "zzz друге", 5.0, 0, 3001, None, 0, owner=m.cur_id())
check("search returns the newest first", res("zzz") == ["zzz друге", "zzz перше"], str(res("zzz")))

# геозони API
g = c.get("/api/geofence").get_json()
check("geofence is off with no zones by default", g["cfg"] == {"on": False, "zones": []})
z = {"name": "Дім", "lat": 50.0, "lon": 30.0, "radius_m": 500}
check("zones can be saved and are written with private rights", c.post("/api/geofence", json={"on": True, "zones": [z]}).get_json()["cfg"]["zones"][0]["name"] == "Дім"
      and os.path.exists(m.GEO_FILE))
check("a bad zone is a 400 and changes nothing", c.post("/api/geofence", json={"zones": [{**z, "radius_m": 1}]}).status_code == 400 and len(c.get("/api/geofence").get_json()["cfg"]["zones"]) == 1)
conn = next(iter(m.conns.values()))
m.geo_tick(conn, time.time())      # перше бачення: лише запам'ятовує
before = len(c.get("/api/events").get_json()["events"])
m.nodes_hold = None
conn.iface.nodesByNum[0x300]["position"] = {"latitude": 50.0, "longitude": 30.0}   # «Горб» перемістився в зону
m.geo_tick(conn, time.time())
evs = c.get("/api/events").get_json()["events"]
check("a node moving into a zone is written to the event log", len(evs) == before + 1 and evs[0]["kind"] == "geo_in" and "Дім" in evs[0]["text"], str(evs[:1]))
check("geo events are known notification kinds", "geo_in" in m.notify.KINDS and "geo_out" in m.notify.KINDS)
for path in ("/api/issues", "/api/coverage", "/api/latest", "/api/messages/search"):
    check(f"{path} is open to the viewer role", path in m.VIEWER_GET)
check("the geofence config (it holds coordinates) is not open to the viewer", "/api/geofence" not in m.VIEWER_GET)

# сторінка й скрипт
page = c.get("/").get_data(as_text=True)
check("the page has all the new blocks and the script", all(k in page for k in ("issuebox", "covpanel", "latestbox", "geobox", "msgq", "msgres", "insights.js")) and c.get("/static/insights.js").status_code == 200)
js = c.get("/static/insights.js").get_data(as_text=True)
check("insights.js inserts foreign text as text, never HTML", "innerHTML" not in js and "insertAdjacentHTML" not in js)
check("insights.js sends nothing to the air (only the geofence settings are written)", js.count("method: 'POST'") == 0 and "/api/send" not in js and "api('/api/geofence', {" in js)
check("the nodes table marks aircraft", "n.aircraft" in page)
check("the chat log stays on the latest messages, also when the tab was hidden while it was drawn", "logPinned" in page and "addEventListener('resize'" in page)
check("a script file may only be named without a path (the editor offers just the listed files)", "st.scripts" in c.get("/static/auto.js").get_data(as_text=True))
check("the chat has one tab per channel (named) and the Ефір tab is gone", "chanIds" in page and "chanNames" in page and 'data-tab="ether"' not in page)
sys.exit(0 if ok else 1)
