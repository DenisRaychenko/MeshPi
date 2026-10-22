import os, sys, tempfile, time, types
tmp = tempfile.mkdtemp()
os.environ["MESHPI_DB"] = os.path.join(tmp, "messages.db")
os.environ.pop("MESHPI_PASSWORD", None)
sys.path.insert(0, sys.argv[1])
import app as m

m.init_db(m.DB_PATH)
now = int(time.time())
ME = 0x100


def node(num, hops, mqtt=False, role=None):
    u = {"id": f"!{num:08x}", "longName": f"Node{num:x}", "shortName": f"N{num & 0xFF:02x}"}
    if role:
        u["role"] = role
    return {"num": num, "user": u, "hopsAway": hops, "viaMqtt": mqtt, "lastHeard": now - 60}


# 0x201 and 0x301 share the last byte 0x01 only with ME's own 0x...; relay byte lookups:
#   0x1A1 (direct neighbour, repeater), 0x2B2 (two hops), 0x3C3 (three hops), 0x4D4 (mqtt-only), 0x5A1 (same last byte as 0x1A1, far)
nodes = {n["num"]: n for n in [node(ME, 0), node(0x1A1, 0, role="ROUTER"), node(0x2B2, 1), node(0x3C3, 2),
                                node(0x4D4, 1, mqtt=True), node(0x5A1, 3), node(0x6E6, 0)]}
m.iface = types.SimpleNamespace(nodesByNum=nodes, nodes={n["user"]["id"]: n for n in nodes.values()},
                                getMyNodeInfo=lambda: {"num": ME, "user": {"id": f"!{ME:08x}"}})
ID = lambda n: f"!{n:08x}"


def pkt(sender, relay=None, hop_start=None, hop_limit=None, snr=5.0, **kw):
    p = {"from": sender, "fromId": ID(sender), "rxSnr": snr, "decoded": {"portnum": "TEXT_MESSAGE_APP"}, **kw}
    if relay is not None:
        p["relayNode"] = relay
    if hop_start is not None:
        p["hopStart"], p["hopLimit"] = hop_start, hop_limit
    return p


ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print("ok  " if cond else "FAIL", name, extra)


def links():
    m.flush_links(True)
    return {(a, b, f, k): (n, ss, sn) for a, b, f, k, n, ss, sn in
            m.db.execute("SELECT src, dst, flow, kind, n, snr_sum, snr_n FROM link")}


m.record_link(pkt(0x6E6, relay=0xE6, hop_start=3, hop_limit=3), m.iface)               # heard directly
m.record_link(pkt(0x6E6, relay=0xE6, hop_start=3, hop_limit=3, snr=7.0), m.iface)
m.record_link(pkt(0x2B2, relay=0xA1, hop_start=3, hop_limit=2), m.iface)               # 1 hop: 2B2 -> 1A1 -> me
m.record_link(pkt(0x3C3, relay=0xA1, hop_start=3, hop_limit=1), m.iface)               # 2 hops: unknown middle
m.record_link(pkt(0x2B2, relay=0xA1, hop_start=3, hop_limit=2, viaMqtt=True), m.iface) # MQTT copy: ignored
m.record_link(pkt(ME, relay=0xA1, hop_start=3, hop_limit=2), m.iface)                  # our own packet echoed back: "down" link
m.record_link(pkt(ME, hop_start=3, hop_limit=3), m.iface)                              # our own packet, no relay: nothing
m.record_link(pkt(0x3C3, relay=0x99, hop_start=3, hop_limit=2), m.iface)               # relay byte matches nobody
m.record_link(pkt(0x2B2, hop_start=3, hop_limit=2), m.iface)                           # no relay byte: nothing to draw
L = links()
check("direct sender -> me, two packets, mean SNR kept",
      L.get((ID(0x6E6), ID(ME), "up", "direct")) == (2, 12.0, 2), str(L.get((ID(0x6E6), ID(ME), "up", "direct"))))
check("relay -> me recorded", L.get((ID(0x1A1), ID(ME), "up", "relay"), (0,))[0] == 2)
check("1 hop: sender -> relay is a direct link", L.get((ID(0x2B2), ID(0x1A1), "up", "direct"), (0,))[0] == 1)
check("2 hops: sender -> relay is 'via' (unknown middle)", L.get((ID(0x3C3), ID(0x1A1), "up", "via"), (0,))[0] == 1)
check("own packet echoed by a neighbour = 'down' link me -> relay",
      L.get((ID(ME), ID(0x1A1), "down", "direct"), (0,))[0] == 1)
check("MQTT copy, unknown relay and no relay byte add nothing", len(L) == 5, str(sorted(L)))

# relay byte 0xA1 matches 1A1 (0 hops) and 5A1 (3 hops): the nearer one wins; two equally near -> ambiguous
check("nearest candidate wins", m.resolve_relay(m.iface, 0xA1, set()) == ID(0x1A1))
nodes[0x7A1] = node(0x7A1, 0)
check("two equally near candidates -> no guess", m.resolve_relay(m.iface, 0xA1, set()) is None)
del nodes[0x7A1]
check("MQTT-only node is never a relay", m.resolve_relay(m.iface, 0xD4, set()) is None)

# traceroute: me -> 1A1 -> 2B2 (down), 2B2 -> 1A1 -> me (up); unknown hop breaks the chain
hop = lambda n, s=None: {"id": ID(n), "name": "x", "snr": s}
m.record_trace({"towards": [hop(ME), hop(0x1A1, 6.0), hop(0x2B2, 3.0)], "back": [hop(0x2B2), hop(0xFFFFFFFF), hop(ME)]})
L = links()
check("trace towards -> 'down' links with SNR (adds to the echo seen earlier: 5.0 + 6.0)",
      L.get((ID(ME), ID(0x1A1), "down", "direct")) == (2, 11.0, 2)
      and L.get((ID(0x1A1), ID(0x2B2), "down", "direct"))[0] == 1)
check("unknown hop in trace is skipped", not any("ffffffff" in a or "ffffffff" in b for a, b, _, _ in L))

# NeighborInfo: node 2B2 says it hears 3C3
info = pkt(0x2B2, hop_start=3, hop_limit=2)
info["decoded"] = {"portnum": "NEIGHBORINFO_APP", "neighborinfo": {"nodeId": 0x2B2, "neighbors": [{"nodeId": 0x3C3, "snr": -4.5}]}}
m.record_link(info, m.iface)
check("NeighborInfo: neighbour -> reporter", links().get((ID(0x3C3), ID(0x2B2), "up", "direct"), (0,))[0] == 1)

c = m.app.test_client()
j = c.get("/api/mesh").get_json()
by = {n["id"]: n for n in j["nodes"]}
check("/api/mesh: day, days and me", j["day"] == m.day_of() and j["me"] == ID(ME) and j["days"][0]["day"] == j["day"])
check("/api/mesh: center node flagged", by[ID(ME)]["me"] and not by[ID(0x2B2)]["me"])
check("/api/mesh: ROUTER role and relay are repeaters", by[ID(0x1A1)]["repeater"] and not by[ID(0x6E6)]["repeater"])
check("/api/mesh: link SNR averaged", next(l for l in j["links"] if l["a"] == ID(0x6E6))["snr"] == 6.0)
check("/api/mesh: no MQTT nodes", ID(0x4D4) not in by)
m.add_link(ID(0x4D4), ID(ME), "up", "direct")
j2 = c.get("/api/mesh").get_json()
check("/api/mesh: MQTT-only node hidden by default, shown with mqtt=1",
      all(ID(0x4D4) not in (l["a"], l["b"]) for l in j2["links"])
      and any(ID(0x4D4) in (l["a"], l["b"]) for l in c.get("/api/mesh?mqtt=1").get_json()["links"]))
check("/api/mesh: other day is empty", c.get("/api/mesh?day=2001-01-01").get_json()["links"] == [])
check("/api/mesh: bad day rejected", c.get("/api/mesh?day=x").status_code == 400)

# archive by day: old rows kept per day, expired ones dropped
m.db.execute("INSERT INTO link VALUES ('2001-01-01', ?, ?, 'up', 'direct', 5, 0, 0, ?)", (ID(0x6E6), ID(ME), 1000000000))
m.db.commit()
m.add_link(ID(0x6E6), ID(ME), "up", "direct")
m.flush_links(True)
check("expired day dropped on flush", c.get("/api/mesh?day=2001-01-01").get_json()["links"] == [])
yday = m.day_of(now - 86400)
m.add_link(ID(0x2B2), ID(0x1A1), "up", "direct", t=now - 86400)
j3 = c.get(f"/api/mesh?day={yday}").get_json()
check("yesterday is a separate day in the archive", len(j3["links"]) == 1 and {d["day"] for d in j3["days"]} >= {yday, m.day_of()})
sys.exit(0 if ok else 1)
