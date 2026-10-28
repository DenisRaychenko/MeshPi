import os, sys, tempfile, time, types
tmp = tempfile.mkdtemp()
os.environ["MESHPI_DB"] = os.path.join(tmp, "messages.db")
os.environ.pop("MESHPI_PASSWORD", None)
sys.path.insert(0, sys.argv[1])
import app as m

m.init_db(m.DB_PATH)
now = int(time.time())
def node(i, hops, mqtt=False, env=None):
    return {"num": i, "user": {"id": f"!{i:08x}", "longName": f"Node{i}", "shortName": f"N{i}"},
            "hopsAway": hops, "viaMqtt": mqtt, "lastHeard": now - 60, "environmentMetrics": env or {}}
nodes = {1: node(1, 1), 2: node(2, 2), 3: node(3, 5), 4: node(4, 3), 5: node(5, 1), 6: node(6, 1, mqtt=True),
         7: node(7, 0, env={"temperature": 18.5, "relativeHumidity": 40})}
m.use_iface(types.SimpleNamespace(nodesByNum=nodes, nodes={n["user"]["id"]: n for n in nodes.values()},
                                  getMyNodeInfo=lambda: {"user": {"id": "!00000007"}}))

def pkt(i, env=None, air=None, hops=None):
    d = {"portnum": "TELEMETRY_APP", "telemetry": {}}
    if env is not None: d["telemetry"]["environmentMetrics"] = env
    if air is not None: d["telemetry"]["airQualityMetrics"] = air
    p = {"from": i, "fromId": f"!{i:08x}", "decoded": d}
    if hops is not None: p["hopStart"] = 3 + hops; p["hopLimit"] = 3          # hops = hopStart - hopLimit
    return p

ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print("ok  " if cond else "FAIL", name, extra)

m.record_env(pkt(1, {"temperature": 20.0, "relativeHumidity": 50, "barometricPressure": 1010.0}, hops=1))
m.record_env(pkt(2, {"temperature": 24.0, "relativeHumidity": 60}, hops=2))
m.record_env(pkt(3, {"temperature": 40.0}, hops=5))                      # too far
m.record_env(pkt(4, {"temperature": 22.0, "barometricPressure": 1014.0}))  # no hop info -> falls back to hopsAway=3
m.record_env(pkt(5, {"temperature": 200.0, "relativeHumidity": 130, "barometricPressure": 5}))  # sensor garbage
m.record_env(pkt(1, {"temperature": 99.0}, hops=1))                      # rate-limited duplicate
m.record_env(pkt(2, air={"pm25Standard": 12, "co2": 450}, hops=2))
m.record_env({"from": 9, "fromId": "!00000009", "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "x"}})

m.record_env(dict(pkt(1, {"temperature": 55.0}, hops=1), viaMqtt=True))        # MQTT-relayed packet: dropped
m.record_env(pkt(6, {"temperature": 5.0}, hops=1))                              # node 6 is flagged viaMqtt in the node list
rows = m.db.execute("SELECT node,k,v FROM env ORDER BY node,k").fetchall()
check("garbage values rejected (node 5 has no rows)", not any(r[0] == "!00000005" for r in rows))
check("rate limit keeps one temperature reading for node 1",
      m.db.execute("SELECT COUNT(*) FROM env WHERE node='!00000001' AND k='temperature'").fetchone()[0] == 1)

s = m.env_summary()
by = {x["id"]: x for x in s["metrics"]}
check("MQTT-flagged node does not enter the average (n stays 3)", by["temperature"]["n"] == 3)
check("temperature = mean of nodes within 3 hops (20, 24, 22; 40 at 5 hops excluded)", by["temperature"]["avg"] == 22.0 and by["temperature"]["n"] == 3, str(by["temperature"]["avg"]))
check("temperature min/max", (by["temperature"]["min"], by["temperature"]["max"]) == (20.0, 24.0))
check("humidity = mean(50, 60)", by["humidity"]["avg"] == 55 and by["humidity"]["n"] == 2)
check("pressure = mean(1010, 1014)", by["pressure"]["avg"] == 1012.0)
check("air quality metrics included", by["pm25"]["avg"] == 12 and by["co2"]["avg"] == 450)
check("metric order is stable", [x["id"] for x in s["metrics"]][:3] == ["temperature", "humidity", "pressure"])
check("sources list excludes far node and sorted by hops", [x["hops"] for x in s["sources"]] == sorted(x["hops"] for x in s["sources"]) and all(x["hops"] <= 3 for x in s["sources"]), str([(x["name"], x["hops"]) for x in s["sources"]]))
check("series present for sparkline (>=1 point)", len(by["temperature"]["series"]) >= 1)

c = m.app.test_client()
j = c.get("/api/env").get_json()
check("/api/env responds", j["max_hops"] == 3 and j["metrics"])
nl = {n["id"]: n for n in c.get("/api/nodes").get_json()}
check("/api/nodes carries env values", nl["!00000001"]["env"]["temperature"] == 20.0 and nl["!00000001"]["env"]["pressure"] == 1010.0)
check("/api/nodes env_t set", nl["!00000001"]["env_t"] >= now)
check("library-remembered env used when DB has none", nl["!00000007"]["env"] == {"temperature": 18.5, "humidity": 40.0}, str(nl["!00000007"]["env"]))
check("node without readings has null env", nl["!00000005"]["env"] is None)
check("viaMqtt exposed as mqtt flag", nl["!00000006"]["mqtt"] is True and nl["!00000001"]["mqtt"] is False)
sys.exit(0 if ok else 1)
