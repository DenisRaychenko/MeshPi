import json, os, sys, tempfile, time, types
tmp = tempfile.mkdtemp()
os.environ["MESHPI_DB"] = os.path.join(tmp, "messages.db")
os.environ["MESHPI_NODES_FILE"] = os.path.join(tmp, "nodes.json")
os.environ["MESHPI_NOTIFY_FILE"] = os.path.join(tmp, "notify.json")
os.environ["MESHPI_NETVIEW_FILE"] = os.path.join(tmp, "netview.json")
os.environ.pop("MESHPI_PASSWORD", None)
sys.path.insert(0, sys.argv[1])
import app as m
import extras as x

m.init_db(m.DB_PATH)
ok = True
def check(name, cb, extra=""):
    global ok
    ok &= bool(cb); print("ok  " if cb else "FAIL", name, extra)

L = lambda a, b, flow="up", kind="direct", n=3: {"a": a, "b": b, "flow": flow, "kind": kind, "n": n, "t": 1, "snr": 5.0}
# ME <- A <- R(роутер) <- B,C ; D з'єднаний лише з ME ; X нікуди не з'єднаний
links = [L("A", "ME"), L("R", "A"), L("B", "R"), L("C", "R"), L("D", "ME"), L("ME", "A", "down")]
v, dist = x.view_graph(links, "R")
check("distances are counted from the new centre, direction ignored", dist == {"R": 0, "A": 1, "B": 1, "C": 1, "ME": 2, "D": 3}, str(dist))
f = {(l["a"], l["b"], l["flow"]) for l in v}
check("a link towards the centre is 'up', away from it 'down'", ("R", "A", "down") in f and ("A", "ME", "down") in f and ("B", "R", "up") in f and ("C", "R", "up") in f)
check("a former 'down' link whose target is closer to the new centre becomes 'up'", ("ME", "A", "up") in f, str(sorted(f)))
check("a link between nodes at equal distance keeps its flow", {(l["a"], l["b"], l["flow"]) for l in x.view_graph([L("B", "C", "down"), L("B", "R"), L("C", "R")], "R")[0]} >= {("B", "C", "down")})
check("nodes not connected to the centre keep their flows", x.view_graph([L("X", "Y", "down")], "R")[0][0]["flow"] == "down" and x.view_graph([], "R") == ([], {"R": 0}))
check("the other fields of a link survive", all(l["n"] == 3 and l["snr"] == 5.0 for l in v))
check("the input is not modified", links[0]["flow"] == "up" and links[1]["flow"] == "up")

# ---- застосунок ----
ME, ROUTER, FAR = 0x100, 0x9E2FF250, 0x300
ID = lambda n: f"!{n:08x}"
now = int(time.time())


class Fake:
    def __init__(self):
        mk = lambda n, name, short, hops, lat, lon, role="CLIENT", fav=False: {"num": n, "user": {"id": ID(n), "longName": name, "shortName": short, "role": role},
                                                                              "hopsAway": hops, "lastHeard": now - 60, "position": {"latitude": lat, "longitude": lon}, "isFavorite": fav}
        self.nodesByNum = {ME: mk(ME, "Home node", "HOME", 0, 50.00, 30.00), ROUTER: mk(ROUTER, "Second Router", "RT2", 1, 50.10, 30.10, "ROUTER", True),
                           0x201: mk(0x201, "Neighbour of router", "NR1", 2, 50.12, 30.12), FAR: mk(FAR, "Far", "FAR", 3, 50.2, 30.2),
                           0x401: mk(0x401, "Mqtt only", "MQ", 5, 1, 1)}
        self.nodesByNum[0x401]["viaMqtt"] = True
        self.nodes = {}

    def getMyNodeInfo(self):
        return self.nodesByNum[ME]


fi = Fake()
m.use_iface(fi)
c = m.app.test_client()
for a, b, flow, kind, cnt in ((ROUTER, ME, "up", "relay", 40), (0x201, ROUTER, "up", "direct", 12), (FAR, ROUTER, "up", "via", 5), (ME, ROUTER, "down", "direct", 6)):
    for _ in range(cnt):
        m.add_link(ID(a), ID(b), flow, kind, 4.0)
m.flush_links(True)

j = c.get("/api/mesh").get_json()
check("off by default: the map is centred on our own node, hops come from the node DB", j["me"] == ID(ME) and not j["view"]["on"]
      and next(n for n in j["nodes"] if n["id"] == ID(ROUTER))["hops"] == 1)
n = c.get("/api/netview").get_json()
check("netview: off, candidates are known non-MQTT nodes, the favourite router first", n["on"] is False and n["node"] is None and n["candidates"][0]["id"] == ID(ROUTER)
      and ID(0x401) not in [x_["id"] for x_ in n["candidates"]] and ID(ME) not in [x_["id"] for x_ in n["candidates"]], str([x_["id"] for x_ in n["candidates"]]))
check("turning it on without a node is refused", c.post("/api/netview", json={"on": True}).status_code == 400)
check("an unknown or malformed node is refused", c.post("/api/netview", json={"on": True, "node": "!deadbeef"}).status_code == 400
      and c.post("/api/netview", json={"on": True, "node": "junk"}).status_code == 400 and c.post("/api/netview", json={"on": "yes", "node": ID(ROUTER)}).status_code == 400)
r = c.post("/api/netview", json={"on": True, "node": ID(ROUTER)})
check("turning it on is saved and reported with what is known about the node", r.status_code == 200 and r.get_json()["on"] and r.get_json()["known"]["direct"] >= 2
      and json.load(open(os.environ["MESHPI_NETVIEW_FILE"]))["main"] == {"on": True, "node": ID(ROUTER)}, str(r.get_json().get("known")))
j = c.get("/api/mesh").get_json()
byid = {n_["id"]: n_ for n_ in j["nodes"]}
check("the map is now centred on the router", j["me"] == ID(ROUTER) and j["view"] == {"on": True, "node": ID(ROUTER), "name": "Second Router", "real_me": ID(ME)} and byid[ID(ROUTER)]["me"] and not byid[ID(ME)]["me"])
check("hop counts from the radio node DB are not used (they are relative to another node)", all(n_["hops"] is None for n_ in j["nodes"]))
check("the origin of the distance rings is the router's position", abs(j["origin"]["lat"] - 50.10) < 1e-6 and abs(j["origin"]["lon"] - 30.10) < 1e-6, str(j["origin"]))
f = {(l["a"], l["b"]): l["flow"] for l in j["links"]}
check("links towards the router are 'up'", f[(ID(0x201), ID(ROUTER))] == "up" and f[(ID(FAR), ID(ROUTER))] == "up")
check("links from the router outward are 'down'", f[(ID(ROUTER), ID(ME))] == "down", str(f))
check("our own node is still on the map", ID(ME) in byid)
check("the toggle is per connection and can be turned off again", c.post("/api/netview", json={"on": False}).get_json()["on"] is False
      and c.get("/api/mesh").get_json()["me"] == ID(ME) and not c.get("/api/mesh").get_json()["view"]["on"])
check("the chosen node is remembered while off", c.get("/api/netview").get_json()["node"] == ID(ROUTER))
open(os.environ["MESHPI_NETVIEW_FILE"], "w").write("{broken")
check("a corrupt file means off", c.get("/api/netview").get_json()["on"] is False and c.get("/api/mesh").status_code == 200)
c.post("/api/netview", json={"on": True, "node": ID(ROUTER)})
r = c.post("/api/netview", json={"on": True, "node": ID(ME)})
mm = c.get("/api/mesh").get_json()
check("pointing the view at our own node changes nothing: the normal view, hops from the node DB", r.status_code == 200 and mm["me"] == ID(ME)
      and not mm["view"]["on"] and next(n for n in mm["nodes"] if n["id"] == ID(ROUTER))["hops"] == 1)
page = c.get("/").get_data(as_text=True)
check("the page has the source section and script", "viewbox" in page and c.get("/static/netview.js").status_code == 200 and "meshpi:mesh-reload" in c.get("/static/mesh.js").get_data(as_text=True))
sys.exit(0 if ok else 1)
