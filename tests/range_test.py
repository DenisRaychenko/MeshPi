import csv, io, os, sys, tempfile, time
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

check("distance Kyiv - Lviv is about 468 km", abs(x.haversine_km(50.4501, 30.5234, 49.8397, 24.0297) - 468) < 3)
check("distance of a point to itself is 0", x.haversine_km(50, 30, 50, 30) == 0)
pk = lambda payload=None, text=None, port="RANGE_TEST_APP", via=False: {"from": 0xA1, "fromId": "!000000a1", "rxSnr": 5.5, "rxRssi": -97, "hopStart": 3, "hopLimit": 3,
                                                                       "rxTime": 1000, "viaMqtt": via, "decoded": {"portnum": port, **({"payload": payload} if payload is not None else {}), **({"text": text} if text else {})}}
check("seq from a bytes payload", x.range_seq(pk(b"seq 17")) == 17)
check("seq from a text payload", x.range_seq(pk(text="seq 42")) == 42)
check("a zero-padded or spaced seq", x.range_seq(pk(b"seq  007")) == 7)
check("other ports, MQTT copies and garbage give None", x.range_seq(pk(b"seq 1", port="TEXT_MESSAGE_APP")) is None and x.range_seq(pk(b"seq 1", via=True)) is None
      and x.range_seq(pk(b"hello")) is None and x.range_seq(pk()) is None)
r = x.range_row(pk(b"seq 3"), 3, {"latitude": 50.01, "longitude": 30.0}, {"latitude": 50.0, "longitude": 30.0})
check("a row has the distance, SNR, hops", abs(r["dist"] - 1.112) < 0.01 and r["snr"] == 5.5 and r["hops"] == 0 and r["node"] == "!000000a1", str(r))
check("without the sender's position the distance is unknown but the row is kept", x.range_row(pk(b"seq 3"), 3, None, {"latitude": 1, "longitude": 1})["dist"] is None)
check("without our position too", x.range_row(pk(b"seq 3"), 3, {"latitude": 1, "longitude": 1}, None)["dist"] is None)


def row(t, seq, dist=1.0, snr=0.0, node="!a"):
    return {"t": t, "node": node, "seq": seq, "snr": snr, "rssi": -100, "hops": 0, "lat": 1.0, "lon": 1.0, "dist": dist}


rows = [row(100, 1, 0.1, 8), row(160, 2, 0.5, 5), row(220, 4, 1.2, -3), row(280, 5, 2.4, -12),   # серія 1: 1..5, пропущено 3
        row(100000, 1, 0.2, 7), row(100060, 2, 0.4, 6),                                      # серія 2 (пауза, seq знову з 1)
        row(300, 10, 0.3, 2, node="!b")]
sm = {s["node"]: s for s in x.range_summary(rows)}
a = sm["!a"]
check("summary: received 6 of 7 expected (one lost) over two series", (a["received"], a["expected"], a["series"]) == (6, 7, 2), str(a))
check("summary: loss, farthest distance with its SNR, SNR range", abs(a["loss"] - 1 / 7) < 1e-9 and a["max_km"] == 2.4 and a["far_snr"] == -12 and (a["snr_min"], a["snr_max"]) == (-12, 8))
check("summary: a single packet is a series of one", (sm["!b"]["received"], sm["!b"]["expected"], sm["!b"]["loss"]) == (1, 1, 0.0))
check("summary of nothing", x.range_summary([]) == [])
nopos = [row(1, 1, None, 3), row(2, 2, None, 4)]
check("without positions: counts work, distance is None", x.range_summary(nopos)[0]["max_km"] is None and x.range_summary(nopos)[0]["received"] == 2)
text = x.range_csv(rows, {"!a": "=EVIL()"})
cells = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
check("CSV: header, all rows, BOM, formula in a name neutralised", cells[0][0] == "time" and len(cells) == 8 and text.startswith("﻿") and cells[1][2] == "'=EVIL()")
nocells = list(csv.reader(io.StringIO(x.range_csv(nopos).lstrip("﻿"))))
check("CSV: distance with three decimals, empty when unknown", cells[1][9] == "0.100" and nocells[1][9] == "", str((cells[1][9], nocells[1][9])))

# ---- застосунок ----
class Fake:
    def __init__(self):
        self.nodesByNum = {0x100: {"num": 0x100, "user": {"id": "!00000100", "longName": "Me"}, "position": {"latitude": 50.0, "longitude": 30.0}},
                           0xA1: {"num": 0xA1, "user": {"id": "!000000a1", "longName": "Walker"}, "position": {"latitude": 50.02, "longitude": 30.0}}}
        self.nodes = {"!000000a1": self.nodesByNum[0xA1]}

    def getMyNodeInfo(self):
        return self.nodesByNum[0x100]


fi = Fake()
m.use_iface(fi)
now = int(time.time())
for k in range(1, 6):
    p = pk(f"seq {k}".encode())
    p["rxTime"], p["rxSnr"] = now - 600 + k * 60, 10 - 4 * k
    m.record_range(p, "main", fi)
m.record_range(pk(b"seq 9", port="TEXT_MESSAGE_APP"), "main", fi)
c = m.app.test_client()
j = c.get("/api/rangetest").get_json()
check("API: five packets recorded, the text message ignored", j["total"] == 5 and len(j["points"]) == 5)
n = j["nodes"][0]
check("API: summary with the node's name, distance 2.2 km and no loss", n["name"] == "Walker" and abs(n["max_km"] - 2.224) < 0.01 and n["loss"] == 0.0, str(n))
check("API: points are [distance, snr, node]", j["points"][0][2] == "!000000a1" and abs(j["points"][0][0] - 2.224) < 0.01)
check("API: hours filter", c.get("/api/rangetest?hours=1").get_json()["total"] == 5 and c.get("/api/rangetest?hours=x").status_code == 400)
r = c.get("/api/rangetest.csv")
check("CSV endpoint: attachment with the node name", r.status_code == 200 and "attachment" in r.headers["Content-Disposition"] and "Walker" in r.get_data(as_text=True))
m.record_range(pk(b"seq 6"), "other", fi)
check("each connection sees only its own results", c.get("/api/rangetest").get_json()["total"] == 5)
m.db.execute("INSERT INTO rangetest (owner, t, node, seq) VALUES ('main', ?, '!x', 1)", (now - m.RANGE_KEEP - 10,))
m.record_range(pk(b"seq 7"), "main", fi)
check("old results are pruned", m.db.execute("SELECT COUNT(*) FROM rangetest WHERE node = '!x'").fetchone()[0] == 0)
page = c.get("/").get_data(as_text=True)
check("the page has the section and script", "rangebox" in page and c.get("/static/rangetest.js").status_code == 200)
sys.exit(0 if ok else 1)
