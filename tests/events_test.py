import os, sys, tempfile, time, types
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

ME = 0x100
ID = lambda n: f"!{n:08x}"
now = int(time.time())


def node(n, name, heard=None, fav=False, mqtt=False):
    d = {"num": n, "user": {"id": ID(n), "shortName": name, "longName": name + " long"}, "isFavorite": fav, "viaMqtt": mqtt}
    if heard is not None:
        d["lastHeard"] = heard
    return d


class Fake:
    def __init__(self):
        self.nodesByNum = {}
        self.nodes = {}

    def put(self, *ns):
        for n in ns:
            self.nodesByNum[n["num"]] = n
            self.nodes[n["user"]["id"]] = n

    def getMyNodeInfo(self):
        return self.nodesByNum[ME]


fi = Fake()
fi.put(node(ME, "Me", now), node(0x201, "Old1", now - 60), node(0x202, "Fav", now - 60, fav=True), node(0x203, "Quiet", now - 99999))
m.use_iface(fi)
c = m.app.test_client()
conn = m.conns["main"]


def events(**q):
    r = c.get("/api/events", query_string=q)
    return r.status_code, r.get_json()


m.events_tick(conn, now)
code, j = events()
check("the first pass only remembers the nodes: no events", code == 200 and j["events"] == [] and j["owner"] == "main")
m.events_tick(conn, now + 30)
check("a quiet second pass adds nothing", events()[1]["events"] == [])

fi.put(node(0x301, "Newbie", now + 40))
m.events_tick(conn, now + 60)
ev = events()[1]["events"]
check("a new node makes one 'new' event with its names", len(ev) == 1 and ev[0]["kind"] == "new" and "Newbie" in ev[0]["text"]
      and "Newbie long" in ev[0]["text"] and ev[0]["node"] == ID(0x301), str(ev))
m.events_tick(conn, now + 90)
check("... and not again", len(events()[1]["events"]) == 1)
fi.put(node(0x302, "Bridge", now + 100, mqtt=True))
m.events_tick(conn, now + 120)
check("a node known only through MQTT is remembered but not announced", len(events()[1]["events"]) == 1)
fi.put(node(0x302, "Bridge", now + 130))  # the same node later heard on the radio is no longer new
m.events_tick(conn, now + 140)
check("... and does not become 'new' later", len(events()[1]["events"]) == 1)

# favourites
t = now + 200
fi.nodesByNum[0x202]["lastHeard"] = t - m.EV_OFFLINE - 600
m.events_tick(conn, t)
ev = events()[1]["events"]
check("a favourite not heard for 30+ minutes: one 'fav_off'", ev[0]["kind"] == "fav_off" and "Fav" in ev[0]["text"] and "★" in ev[0]["text"]
      and "40 хв" in ev[0]["text"], ev[0]["text"])
m.events_tick(conn, t + 20)
check("... only once", sum(e["kind"] == "fav_off" for e in events()[1]["events"]) == 1)
m.ev_state.clear()   # the panel restarted: the detector remembers from the journal, not from memory
m.events_tick(conn, t + 40)
check("after a restart of the panel there is no second 'fav_off'", sum(e["kind"] == "fav_off" for e in events()[1]["events"]) == 1)
fi.nodesByNum[0x202]["lastHeard"] = t + 50
m.events_tick(conn, t + 60)
ev = events()[1]["events"]
check("the favourite is heard again: 'fav_on'", ev[0]["kind"] == "fav_on" and "знову" in ev[0]["text"], ev[0]["text"])
m.events_tick(conn, t + 80)
check("... only once", sum(e["kind"] == "fav_on" for e in events()[1]["events"]) == 1)
fi.nodesByNum[0x202]["lastHeard"] = t + 50 - 10 * m.EV_OFFLINE
m.events_tick(conn, t + 100)
check("it can disappear again later", [e["kind"] for e in events()[1]["events"]][:2] == ["fav_off", "fav_on"])
check("a non-favourite that is silent causes nothing", not any(e["node"] == ID(0x203) for e in events()[1]["events"]))
fi.put(node(0x204, "NeverHeard", None, fav=True), node(0x205, "Zero", 0, fav=True))
m.events_tick(conn, t + 120)
check("a favourite with no lastHeard at all is not reported missing", not any(
    e["node"] in (ID(0x204), ID(0x205)) and e["kind"].startswith("fav") for e in events()[1]["events"]))
check("own node is never announced", not any(e["node"] == ID(ME) for e in events()[1]["events"]))

# the API
allv = events()[1]["events"]
check("newest first", [e["id"] for e in allv] == sorted((e["id"] for e in allv), reverse=True))
check("?after returns only newer events", [e["id"] for e in events(after=allv[1]["id"])[1]["events"]] == [allv[0]["id"]])
check("?limit", len(events(limit=2)[1]["events"]) == 2)
check("bad parameters are refused", events(after="x")[0] == 400 and events(limit="y")[0] == 400)
check("events have the fields the page needs", set(allv[0]) == {"id", "t", "kind", "node", "text"})

# separate journals per connection
c.post("/api/conns", json={"action": "add", "name": "Дача", "host": "10.0.0.2", "port": 4403})
cid = next(x["id"] for x in c.get("/api/conns").get_json()["conns"] if x["id"] != "main")
check("a second connection has its own, empty journal", c.post("/api/conns", json={"action": "select", "id": cid}).status_code == 200
      and events()[1] == {"owner": cid, "events": []})
c.post("/api/conns", json={"action": "select", "id": "main"})
check("... and the first keeps its own", len(events()[1]["events"]) == len(allv))

# a pruned journal
for k in range(m.EV_KEEP + 30):
    m.add_event("main", "new", ID(0x900 + k), f"Новий вузол {k}")
n = m.db.execute("SELECT COUNT(*) FROM event WHERE owner='main'").fetchone()[0]
check("the journal keeps the latest EV_KEEP events", n == m.EV_KEEP and events(limit=500)[1]["events"][0]["text"].endswith(str(m.EV_KEEP + 29)), str(n))
check("a very long text is cut", len(m.db.execute("SELECT text FROM event ORDER BY id DESC LIMIT 1").fetchone()[0]) <= 200
      and m.add_event("main", "new", "!1", "x" * 500) is None
      and len(m.db.execute("SELECT text FROM event ORDER BY id DESC LIMIT 1").fetchone()[0]) == 200)

# an interface without nodes (offline) is not an error
conn.iface = None
check("offline connection: the pass does nothing", m.events_tick(conn, now) is None)
check("age text", m.age_text(120) == "2 хв" and m.age_text(7200) == "2 год" and m.age_text(3 * 86400) == "3 дн" and m.age_text(-5) == "0 хв")

# page
page = c.get("/").get_data(as_text=True)
check("the page has the tab, the table and the script", 'data-tab="events"' in page and 'id="evrows"' in page and "/static/events.js" in page)
js = c.get("/static/events.js")
check("the script is served and uses textContent only", js.status_code == 200 and "innerHTML" not in js.get_data(as_text=True))
sys.exit(0 if ok else 1)
