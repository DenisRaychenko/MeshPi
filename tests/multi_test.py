import json, os, sqlite3, sys, tempfile, time, types
tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "messages.db")
os.environ["MESHPI_DB"] = db_path
os.environ.pop("MESHPI_PASSWORD", None)

# a database written by the single-node version: no owner columns, metric/link with the old primary keys
old = sqlite3.connect(db_path)
old.execute("CREATE TABLE msg (id INTEGER PRIMARY KEY, t INT, sender TEXT, name TEXT, dest TEXT, ch INT, text TEXT, snr REAL, out INT)")
old.execute("INSERT INTO msg (t, sender, name, dest, ch, text, snr, out) VALUES (1, '!1', 'A', '^all', 0, 'old message', 5, 0)")
old.execute("CREATE TABLE metric (t INT PRIMARY KEY, chan REAL, air REAL, nodes INT)")
old.execute("INSERT INTO metric VALUES (100, 1.5, 0.5, 7)")
old.execute("CREATE TABLE link (day TEXT, src TEXT, dst TEXT, flow TEXT, kind TEXT, n INT, snr_sum REAL, snr_n INT, t INT,"
            " PRIMARY KEY (day, src, dst, flow, kind))")
old.execute("INSERT INTO link VALUES ('2026-01-01', '!a', '!b', 'up', 'direct', 3, 9, 3, 50)")
old.execute("CREATE TABLE env (t INT, node TEXT, hops INT, k TEXT, v REAL)")
old.execute("INSERT INTO env VALUES (1, '!a', 1, 'temperature', 20)")
old.execute("CREATE TABLE probe (node TEXT, kind TEXT, t REAL, res TEXT)")
old.execute("INSERT INTO probe VALUES ('!a', 'trace', 1, '{}')")
old.commit(); old.close()

sys.path.insert(0, sys.argv[1])
import app as m

m.init_db(m.DB_PATH)
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print("ok  " if cond else "FAIL", name, extra)

q = lambda sql, *a: m.db.execute(sql, a).fetchall()
check("legacy rows move to owner 'main' in every table",
      all(q(f"SELECT COUNT(*) FROM {t} WHERE owner = 'main'")[0][0] == 1 for t in ("msg", "metric", "link", "env", "probe")))
check("metric and link keep their data after the primary key change",
      q("SELECT chan, nodes FROM metric")[0] == (1.5, 7) and q("SELECT n, snr_sum FROM link")[0] == (3, 9))
m.init_db(m.DB_PATH)
check("second init is a no-op (migration is idempotent)", q("SELECT COUNT(*) FROM metric")[0][0] == 1, str(q("SELECT * FROM metric")))
m.use_iface(types.SimpleNamespace(nodesByNum={}, nodes={}, getMyNodeInfo=lambda: {"user": {"id": "!1"}}))

c = m.app.test_client()
j = c.get("/api/conns").get_json()
check("starts with one connection 'main' built from MESH_HOST/MESH_PORT", [x["id"] for x in j["conns"]] == ["main"] and j["current"] == "main")

r = c.post("/api/conns", json={"action": "add", "name": "Router 2", "host": "10.0.0.2", "port": 4403})
j = r.get_json()
check("add a second node", r.status_code == 200 and [x["id"] for x in j["conns"]] == ["main", "router-2"], str(j))
check("list is persisted in nodes.json", [x["id"] for x in json.load(open(m.NODES_FILE))] == ["main", "router-2"])
j = c.post("/api/conns", json={"action": "add", "name": "Router 2", "host": "10.0.0.3", "port": 4403}).get_json()
check("same name gets a unique id", [x["id"] for x in j["conns"]] == ["main", "router-2", "router-2-2"])
for bad, why in (({"name": "", "host": "a", "port": 1}, "empty name"), ({"name": "x", "host": "bad host!", "port": 1}, "bad host"),
                 ({"name": "x", "host": "a", "port": 0}, "port 0"), ({"name": "x", "host": "a", "port": "4403"}, "port as text")):
    check(f"validation: {why}", c.post("/api/conns", json={"action": "add", **bad}).status_code == 400)
check("unknown action rejected", c.post("/api/conns", json={"action": "boom"}).status_code == 400)

# per-connection data
m.add_msg("!2", "Bob", "^all", 0, "to main", 1.0, 0, owner="main")
m.add_msg("!3", "Eve", "^all", 0, "to router", 2.0, 0, owner="router-2")
m.add_link("!a", "!b", "up", "direct", 4.0, owner="router-2")
m.flush_links(True)
msgs = lambda: [x["text"] for x in c.get("/api/messages").get_json()]
check("default view is the first node: its history only", msgs() == ["old message", "to main"], str(msgs()))
check("select the second node", c.post("/api/conns", json={"action": "select", "id": "router-2"}).get_json()["current"] == "router-2")
check("the session now shows the second node's history only", msgs() == ["to router"], str(msgs()))
mesh = c.get("/api/mesh?day=" + m.day_of()).get_json()
check("links are per node too", [(l["a"], l["b"]) for l in mesh["links"]] == [("!a", "!b")])
c2 = m.app.test_client()
check("another browser session is unaffected", [x["text"] for x in c2.get("/api/messages").get_json()] == ["old message", "to main"])
check("select unknown id rejected", c.post("/api/conns", json={"action": "select", "id": "nope"}).status_code == 404)
check("pong rate limit is per node", True)

# the same message id on two nodes is not a duplicate
check("dedupe by packet id is per node", m.add_msg("!9", "Z", "^all", 0, "x", 1, 0, pid=77, owner="main")
      and m.add_msg("!9", "Z", "^all", 0, "x", 1, 0, pid=77, owner="router-2")
      and not m.add_msg("!9", "Z", "^all", 0, "x", 1, 0, pid=77, owner="router-2"))

# interface -> owner
check("owner_of reads the interface owner", m.owner_of(types.SimpleNamespace(meshpi_owner="router-2")) == "router-2"
      and m.owner_of(types.SimpleNamespace()) == "main")

# removal
j = c.post("/api/conns", json={"action": "remove", "id": "router-2-2"}).get_json()
check("remove a node", [x["id"] for x in j["conns"]] == ["main", "router-2"])
j = c.post("/api/conns", json={"action": "update", "id": "router-2", "name": "Renamed", "host": "10.0.0.9", "port": 4404}).get_json()
check("update name and address", next(x for x in j["conns"] if x["id"] == "router-2")["host"] == "10.0.0.9")
c.post("/api/conns", json={"action": "remove", "id": "router-2"})
check("session falls back to the first node when its node was removed", msgs() == ["old message", "to main", "x"], str(msgs()))
check("the last connection cannot be removed", c.post("/api/conns", json={"action": "remove", "id": "main"}).status_code == 400)
sys.exit(0 if ok else 1)
