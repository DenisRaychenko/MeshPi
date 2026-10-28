import base64, os, sys, tempfile, threading, time, types
tmp = tempfile.mkdtemp()
os.environ["MESHPI_DB"] = os.path.join(tmp, "messages.db")
os.environ.pop("MESHPI_PASSWORD", None)
sys.path.insert(0, sys.argv[1])
import app as m
import admin
from meshtastic.protobuf import admin_pb2, localonly_pb2, mesh_pb2

m.init_db(m.DB_PATH)
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print("ok  " if cond else "FAIL", name, extra)

ME, PEER = 0x100, 0x2B2
PEER_ID = f"!{PEER:08x}"


class Fake:
    def __init__(self):
        self.sent, self.positions, self.handlers = [], [], {}
        self.script = None                           # функція(admin_msg/packet) -> відповідь або None
        self.nodesByNum = {ME: {"num": ME, "user": {"id": f"!{ME:08x}", "longName": "Me"}},
                           PEER: {"num": PEER, "user": {"id": PEER_ID, "longName": "Peer", "role": "ROUTER"},
                                  "isFavorite": True, "isIgnored": False, "isMuted": True, "lastHeard": int(time.time())}}
        self.nodes = {n["user"]["id"]: n for n in self.nodesByNum.values()}
        self.localNode = types.SimpleNamespace(nodeNum=ME, localConfig=localonly_pb2.LocalConfig(), moduleConfig=localonly_pb2.LocalModuleConfig(),
                                               channels=[object()], getURL=lambda includeAll=True: "https://meshtastic.org/e/#CgMSAQE",
                                               _getAdminChannelIndex=lambda: 0)
        self._e = {}

    def getMyNodeInfo(self):
        return self.nodesByNum[ME]

    def _getOrCreateByNum(self, n):
        return self._e.setdefault(n, {})

    def sendData(self, data, destinationId=None, portNum=None, wantAck=False, wantResponse=False, onResponse=None,
                 onResponseAckPermitted=False, channelIndex=0, pkiEncrypted=False, replyId=None, hopLimit=None):
        pkt = types.SimpleNamespace(id=4242, data=data, dest=destinationId, ack=wantAck, cb=onResponse, ackok=onResponseAckPermitted)
        self.sent.append(pkt)
        if self.script and onResponse:
            r = self.script(data)
            if r is not None:
                threading.Timer(0.02, onResponse, args=(r,)).start()
        return pkt

    def sendText(self, text, destinationId=None, channelIndex=0, replyId=None):
        pkt = types.SimpleNamespace(id=777, text=text, dest=destinationId)
        self.sent.append(pkt)
        return pkt

    def _generatePacketId(self):
        return 9001

    def _sendPacket(self, p, to):
        self.sent.append(types.SimpleNamespace(id=p.id, emoji=True, dest=to))
        return p

    def sendPosition(self, lat, lon, alt, destinationId="^all"):
        self.positions.append((lat, lon, alt, destinationId))


fi = Fake()
m.use_iface(fi)
c = m.app.test_client()

# ---- статус доставки ----
r = c.post("/api/send", json={"text": "hi", "to": PEER_ID, "ch": 0})
dm = fi.sent[-1]
check("DM is sent with an ack request and an ack-permitted handler", r.status_code == 200 and dm.ack and dm.ackok and dm.cb)
row = lambda: next(x for x in c.get("/api/messages").get_json() if x["pid"] == 4242)
check("a fresh DM is 'sent' (ack 0)", row()["ack"] == 0)
dm.cb({"fromId": f"!{ME:08x}", "decoded": {"portnum": "ROUTING_APP", "requestId": 4242, "routing": {"errorReason": "NONE"}}})
check("ack from our own node means a neighbour relayed it (1)", row()["ack"] == 1)
dm.cb({"fromId": PEER_ID, "decoded": {"portnum": "ROUTING_APP", "requestId": 4242, "routing": {"errorReason": "NONE"}}})
check("ack from the destination means delivered (2)", row()["ack"] == 2)
dm.cb({"fromId": PEER_ID, "decoded": {"portnum": "ROUTING_APP", "requestId": 4242, "routing": {"errorReason": "MAX_RETRANSMIT"}}})
check("a later failure does not undo a delivery", row()["ack"] == 2)
c.post("/api/send", json={"text": "second", "to": PEER_ID, "ch": 0})
dm2 = fi.sent[-1]
dm2.id = 4243
m.add_msg(None, "Ви", PEER_ID, 0, "x", None, 1, 4300, None, 0, ack=0)
m.set_ack("main", 4300, 3)
check("a NAK marks the message failed (3)", next(x for x in c.get("/api/messages").get_json() if x["pid"] == 4300)["ack"] == 3)
r = c.post("/api/send", json={"text": "to all", "to": "^all", "ch": 0})
check("broadcast text is not tracked (ack stays null)", fi.sent[-1].text == "to all" and r.status_code == 200
      and next(x for x in c.get("/api/messages").get_json() if x["pid"] == 777)["ack"] is None)
c.post("/api/send", json={"text": "👍", "to": PEER_ID, "ch": 0, "reply": 4242, "emoji": True})
react = [x for x in c.get("/api/messages").get_json() if x["emoji"]]
check("a reaction is stored and not tracked", len(react) == 1 and react[0]["ack"] is None)

# ---- позиція ----
def pos_reply(data):
    if isinstance(data, mesh_pb2.Position):
        return {"decoded": {"portnum": "POSITION_APP", "position": {"latitudeI": 492000000, "longitudeI": 314000000, "altitude": 150}}}
fi.script = pos_reply
def job(**body):
    r = c.post("/api/admin/job", json=body)
    if r.status_code != 200:
        return r.status_code, r.get_json()
    for _ in range(200):
        j = c.get("/api/admin/job?id=" + r.get_json()["job"]).get_json()
        if j["state"] != "running":
            return 200, j
        time.sleep(0.05)
code, j = job(op="request_position", node=PEER_ID, target="local")
check("request position returns coordinates", j["state"] == "done" and abs(j["result"]["lat"] - 49.2) < 1e-6 and j["result"]["alt"] == 150, str(j.get("error")))
fi.script = lambda d: {"decoded": {"portnum": "POSITION_APP", "position": {}}}
code, j = job(op="request_position", node=PEER_ID, target="local")
check("a node without position gives a clear error", j["state"] == "error" and "позиці" in j["error"], str(j.get("error")))
check("request position validates the node id", job(op="request_position", node="bad", target="local")[0] == 400)
code, j = job(op="send_position", node="^all", lat=49.2, lon=31.4, alt=120, target="local")
check("send my position", j["state"] == "done" and fi.positions[-1] == (49.2, 31.4, 120, "^all"))
code, j = job(op="send_position", node=PEER_ID, lat=95, lon=31.4, target="local")
check("send position validates the range", j["state"] == "error")

# ---- готові повідомлення ----
def canned_reply(data):
    if isinstance(data, admin_pb2.AdminMessage) and data.WhichOneof("payload_variant") == "get_canned_message_module_messages_request":
        r = admin_pb2.AdminMessage()
        r.get_canned_message_module_messages_response = "OK|Йду|Допоможіть"
        return {"decoded": {"portnum": "ADMIN_APP", "admin": {"raw": r}}}
fi.script = canned_reply
code, j = job(op="canned", target="local")
check("canned messages read", j["state"] == "done" and j["result"]["text"] == "OK|Йду|Допоможіть", str(j.get("error")))
n = len(fi.sent)
code, j = job(op="set_canned", target="local", text="Так|Ні")
check("canned messages written", j["state"] == "done" and fi.sent[-1].data.set_canned_message_module_messages == "Так|Ні" and len(fi.sent) == n + 1)
code, j = job(op="set_canned", target="local", text="x" * 300)
check("canned messages over 200 bytes rejected", j["state"] == "error")

# ---- QR каналів ----
j = c.get("/api/channel-qr").get_json()
svg = base64.b64decode(j["qr"].split(",", 1)[1]).decode()
check("channel QR: url and an SVG image", j["url"].startswith("https://meshtastic.org/e/#") and svg.startswith("<svg") and "path" in svg, svg[:40])
fi.localNode.channels = None
check("channel QR before channels are loaded: 503", c.get("/api/channel-qr").status_code == 503)

# ---- журнал пакетів ----
for k in range(5):
    m.on_packet({"from": PEER, "fromId": PEER_ID, "to": 0xFFFFFFFF, "rxTime": 1000 + k, "rxSnr": 5.0 + k, "rxRssi": -80, "hopStart": 3, "hopLimit": 2,
                 "relayNode": 0xB2, "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "x"}}, fi)
m.on_packet({"from": PEER, "fromId": PEER_ID, "to": 0xFFFFFFFF, "rxTime": 2000, "viaMqtt": True}, fi)
rows = c.get("/api/packets?limit=3").get_json()
check("packet log: newest first, limited", len(rows) == 3 and rows[0]["t"] == 2000 and rows[1]["t"] == 1004, str([r["t"] for r in rows]))
check("packet log: hops, relay, name, mqtt flag", rows[0]["mqtt"] and rows[1]["hops"] == 1 and rows[1]["relay"] == "b2" and rows[1]["from_name"] == "Peer")
check("packet log: encrypted packet has no portnum", rows[0]["port"] == "ЗАШИФРОВАНО" and rows[1]["port"] == "TEXT_MESSAGE_APP")
check("packet log is per connection", m.pkt_log.keys() == {"main"} and c.get("/api/packets?limit=x").status_code == 400)

# ---- /api/status не падає, коли бібліотека поклала у вузол сирі protobuf-об'єкти ----
fi.nodesByNum[ME]["user"]["raw"] = mesh_pb2.User(long_name="Me")
fi.nodesByNum[ME]["deviceMetrics"] = {"batteryLevel": 80, "raw": mesh_pb2.User()}
r = c.get("/api/status")
sj = r.get_json()
check("/api/status survives raw protobuf objects in the node info", r.status_code == 200 and sj["node"].get("longName") == "Me" and "raw" not in sj["node"]
      and sj["metrics"] == {"batteryLevel": 80}, str(r.status_code))
check("plain() drops raw protobufs recursively and keeps JSON values", m.plain({"a": 1, "raw": mesh_pb2.User(), "b": {"raw": 1, "c": [1, mesh_pb2.User(), "x"]}, "u": mesh_pb2.User()})
      == {"a": 1, "b": {"c": [1, "x"]}})
del fi.nodesByNum[ME]["user"]["raw"]

# ---- прапорці вузлів ----
nl = {n["id"]: n for n in c.get("/api/nodes").get_json()}
check("/api/nodes: favourite, ignored, muted flags and role", nl[PEER_ID]["fav"] and not nl[PEER_ID]["ignored"] and nl[PEER_ID]["muted"] and nl[PEER_ID]["role"] == "ROUTER")
sys.exit(0 if ok else 1)
