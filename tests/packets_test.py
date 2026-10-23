import csv, io, os, sys, tempfile, time, types
tmp = tempfile.mkdtemp()
os.environ["MESHPI_DB"] = os.path.join(tmp, "messages.db")
os.environ["MESHPI_NODES_FILE"] = os.path.join(tmp, "nodes.json")
os.environ["MESHPI_NOTIFY_FILE"] = os.path.join(tmp, "notify.json")
os.environ.pop("MESHPI_PASSWORD", None)
sys.path.insert(0, sys.argv[1])
import app as m
import extras as x

m.init_db(m.DB_PATH)
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print("ok  " if cond else "FAIL", name, extra)

ROWS = [
    {"t": 1000, "from": "!0000000a", "from_name": "Alpha", "to": "^all", "port": "TEXT_MESSAGE_APP", "snr": 6.0, "rssi": -80, "hops": 0, "relay": None, "mqtt": False, "ch": 0, "id": 1},
    {"t": 1010, "from": "!0000000b", "from_name": "Bravo", "to": "!0000000a", "port": "TELEMETRY_APP", "snr": -12.5, "rssi": -118, "hops": 2, "relay": "0a", "mqtt": False, "ch": 0, "id": 2},
    {"t": 1020, "from": "!0000000c", "from_name": "=HYPERLINK(\"http://evil\")", "to": "^all", "port": "POSITION_APP", "snr": None, "rssi": None, "hops": 1, "relay": None, "mqtt": True, "ch": 0, "id": 3},
    {"t": 1030, "from": "!0000000d", "from_name": None, "to": "^all", "port": "ЗАШИФРОВАНО", "snr": 2.0, "rssi": -100, "hops": 3, "relay": "0b", "mqtt": False, "ch": 1, "id": 4},
]
ids = lambda **a: [r["id"] for r in x.filter_packets(ROWS, a)]
check("no filters returns everything", ids() == [1, 2, 3, 4])
check("by port", ids(port="TELEMETRY_APP") == [2])
check("by node name, case-insensitive", ids(node="alph") == [1] and ids(node="ALPHA") == [1])
check("by node id, also as the destination", ids(node="0000000a") == [1, 2])
check("by minimum SNR (packets without SNR are dropped)", ids(snr_min="0") == [1, 4])
check("by maximum SNR", ids(snr_max="0") == [2])
check("by hops", ids(hops="2") == [2] and ids(hops="0") == [1])
check("without MQTT", ids(nomqtt="1") == [1, 2, 4])
check("filters combine", ids(snr_min="0", nomqtt="1", port="ЗАШИФРОВАНО") == [4])
for bad in ({"snr_min": "abc"}, {"snr_max": "nan"}, {"hops": "x"}):
    try:
        x.filter_packets(ROWS, bad)
        check("a bad filter value is an error: " + str(bad), False)
    except ValueError:
        check("a bad filter value is an error: " + str(bad), True)

text = x.packets_csv(ROWS)
check("CSV starts with a BOM for Excel", text.startswith("﻿"))
rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
check("CSV: header and one line per packet", rows[0] == list(x.CSV_COLUMNS) and len(rows) == 5)
check("CSV: numbers stay numbers, empty stays empty", rows[1][6] == "6.0" and rows[3][6] == "" and rows[2][6] == "-12.5")
check("CSV: a formula in a name is neutralised", rows[3][3].startswith("'="), rows[3][3])
check("CSV: a negative number is not mangled", rows[2][7] == "-118")
check("CSV: Ukrainian text survives", rows[4][5] == "ЗАШИФРОВАНО")

# ---- API ----
class Fake:
    def __init__(self):
        self.nodes = {"!0000000a": {"user": {"longName": "Alpha-long"}}}
        self.nodesByNum = {}

    def getMyNodeInfo(self):
        return {"user": {"id": "!00000001"}}


m.use_iface(Fake())
for r in ROWS:
    m.pkt_log.setdefault("main", __import__("collections").deque(maxlen=m.PKT_LOG)).append({k: v for k, v in r.items() if k != "from_name"})
c = m.app.test_client()
j = c.get("/api/packets?port=TELEMETRY_APP").get_json()
check("API filters by port, newest first overall", [r["id"] for r in j] == [2] and c.get("/api/packets").get_json()[0]["id"] == 4)
check("API filters by node and SNR", [r["id"] for r in c.get("/api/packets?node=alpha&snr_min=1").get_json()] == [1])
check("API: a bad filter is a 400 with a reason", c.get("/api/packets?snr_min=zz").status_code == 400 and "snr_min" in c.get("/api/packets?snr_min=zz").get_json()["error"])
check("API: the limit still applies", len(c.get("/api/packets?limit=2").get_json()) == 2)
r = c.get("/api/packets.csv?nomqtt=1")
body = r.get_data(as_text=True)
check("CSV endpoint: type, attachment name, no caching", r.status_code == 200 and r.mimetype == "text/csv" and "attachment; filename=\"meshpi-packets-" in r.headers["Content-Disposition"]
      and r.headers["Cache-Control"] == "no-store")
check("CSV endpoint: filtered and with node names from the node list", body.count("\r\n") == 4 and "Alpha-long" in body and "evil" not in body, body[:120])
check("CSV endpoint: bad filter is a 400, not a broken file", c.get("/api/packets.csv?hops=q").status_code == 400)
page = c.get("/").get_data(as_text=True)
check("the page has the filter controls and the CSV link", all(k in page for k in ('id="pktport"', 'id="pktnode"', 'id="pktcsv"', 'id="pktsnr"')))
check("the log keeps up to MESHPI_PKT_LOG packets (default 1000)", m.PKT_LOG == 1000)
sys.exit(0 if ok else 1)
