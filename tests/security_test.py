import base64, json, os, sys, tempfile, types
tmp = tempfile.mkdtemp()
os.environ["MESHPI_DB"] = os.path.join(tmp, "messages.db")
os.environ["MESHPI_NODES_FILE"] = os.path.join(tmp, "nodes.json")
os.environ["MESHPI_NOTIFY_FILE"] = os.path.join(tmp, "notify.json")
os.environ.pop("MESHPI_PASSWORD", None)
sys.path.insert(0, sys.argv[1])
import app as m
import extras as x
from meshtastic.protobuf import channel_pb2, localonly_pb2

m.init_db(m.DB_PATH)
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print("ok  " if cond else "FAIL", name, extra)

b64 = lambda raw: base64.b64encode(raw).decode()
K1, K2, WEAK = b64(bytes(range(32))), b64(bytes(range(1, 33))), b64(b"\x00" * 32)
N = lambda i, name, key="", mqtt=False: {"id": f"!{i:08x}", "name": name, "key": key, "mqtt": mqtt}
CH0 = {"index": 0, "name": "LongFast", "role": "PRIMARY", "psk": b"\x01", "uplink": False, "downlink": False, "precision": 0}
CFG = {"mqtt_on": False, "ok_to_mqtt": False, "admin_keys": 1, "admin_channel": False, "managed": False}
ids = lambda r: [f["id"] for f in r["findings"]]
lv = lambda r, i: next(f["level"] for f in r["findings"] if f["id"] == i)

r = x.security_audit([N(1, "Me", K1), N(2, "A", K2)], "!00000001", [CH0], CFG)
check("a normal setup: only an informational note about the open default channel", r["status"] == "ok" and lv(r, "defpsk0") == "info", str(ids(r)))
r = x.security_audit([N(1, "Me", K1), N(2, "Клон1", K2), N(3, "Клон2", K2), N(4, "Клон3", K2)], "!00000001", [CH0], CFG)
d = next(f for f in r["findings"] if f["id"] == "dupkey")
check("identical public keys are critical and list the nodes", r["status"] == "crit" and d["items"] == ["Клон1", "Клон2", "Клон3"], str(d))
r = x.security_audit([N(1, "Me", K1), N(2, "Слабкий", WEAK)], "!00000001", [CH0], CFG)
check("a trivially weak key is critical", lv(r, "trivialkey") == "crit")
r = x.security_audit([N(1, "Me", "")], "!00000001", [CH0], CFG)
check("our own node without a key is a warning", lv(r, "nokey") == "warn")
r = x.security_audit([N(1, "Me", K1), N(2, "Old", "")], "!00000001", [CH0], CFG)
check("other nodes without keys are not a finding (old firmware is common)", "nokey" not in ids(r))
sec = {"index": 1, "name": "Друзі", "role": "SECONDARY", "psk": b"\x01", "uplink": False, "downlink": False, "precision": 0}
r = x.security_audit([N(1, "Me", K1)], "!00000001", [CH0, sec], CFG)
check("a secondary channel with the default key is a warning", lv(r, "defpsk1") == "warn" and r["status"] == "warn")
sec["psk"] = bytes(range(32))
check("a random 32-byte key is fine", "defpsk1" not in ids(x.security_audit([N(1, "Me", K1)], "!00000001", [CH0, sec], CFG)))
sec["psk"] = b""
check("a channel without a key is a warning", lv(x.security_audit([N(1, "Me", K1)], "!00000001", [CH0, sec], CFG), "nopsk1") == "warn")
sec["role"] = "DISABLED"
check("disabled channels are skipped", not any(i.endswith("1") for i in ids(x.security_audit([N(1, "Me", K1)], "!00000001", [CH0, sec], CFG))))
mq = {**CH0, "uplink": True}
check("MQTT uplink with the default key is a warning, with a private key only a note",
      lv(x.security_audit([N(1, "Me", K1)], "!00000001", [mq], {**CFG, "mqtt_on": True}), "uplink0") == "warn"
      and lv(x.security_audit([N(1, "Me", K1)], "!00000001", [{**mq, "psk": bytes(range(32))}], {**CFG, "mqtt_on": True}), "uplink0") == "info")
check("uplink flag without the MQTT module on is not reported", "uplink0" not in ids(x.security_audit([N(1, "Me", K1)], "!00000001", [mq], CFG)))
check("legacy admin channel without admin keys is a warning", lv(x.security_audit([N(1, "Me", K1)], "!00000001", [CH0], {**CFG, "admin_keys": 0, "admin_channel": True}), "legacyadmin") == "warn")
check("no admin keys at all is reported as safe", lv(x.security_audit([N(1, "Me", K1)], "!00000001", [CH0], {**CFG, "admin_keys": 0}), "noadmin") == "info")
check("full-precision position on the open channel is noted", "precision" in ids(x.security_audit([N(1, "Me", K1)], "!00000001", [{**CH0, "precision": 32}], CFG)))
check("accepts the hex form of a key too", x.is_default_psk("d4f1bb3a20290759f0bcffabcf4e6901") and x.is_default_psk(b"\x01") and not x.is_default_psk(b"\x02"))

# ---- API з вузлом із справжніми protobuf ----
ME = 0x100
ln = types.SimpleNamespace(localConfig=localonly_pb2.LocalConfig(), moduleConfig=localonly_pb2.LocalModuleConfig(), channels=[])
ln.localConfig.security.admin_key.append(bytes(range(32)))
ln.moduleConfig.mqtt.enabled = True
c0 = channel_pb2.Channel(index=0, role=channel_pb2.Channel.Role.PRIMARY)
c0.settings.psk = b"\x01"; c0.settings.uplink_enabled = True; c0.settings.name = "LongFast"
c1 = channel_pb2.Channel(index=1, role=channel_pb2.Channel.Role.SECONDARY)
c1.settings.psk = bytes(range(32)); c1.settings.name = "Свої"
ln.channels = [c0, c1]


class Fake:
    def __init__(self):
        mk = lambda n, name, key: {"num": n, "user": {"id": f"!{n:08x}", "shortName": name, "publicKey": key}}
        self.nodesByNum = {ME: mk(ME, "Me", K1), 0x201: mk(0x201, "A", K2), 0x202: mk(0x202, "B", K2)}
        self.nodes = {}
        self.localNode = ln

    def getMyNodeInfo(self):
        return self.nodesByNum[ME]


m.use_iface(Fake())
c = m.app.test_client()
j = c.get("/api/security").get_json()
check("API: duplicates among the real node list are found", any(f["id"] == "dupkey" and f["items"] == ["A", "B"] for f in j["findings"]), str(ids(j)))
check("API: MQTT uplink on the default key is flagged", any(f["id"] == "uplink0" and f["level"] == "warn" for f in j["findings"]))
check("API: counts nodes and keys", j["nodes"] == 3 and j["keyed"] == 3 and j["status"] == "crit")
check("API: the response holds no key material", K1 not in json.dumps(j) and b64(bytes(range(32))) not in json.dumps(j))
m.conns["main"].iface = None
check("API: offline gives 503", c.get("/api/security").status_code == 503)
check("the page has the security section and script", "secbox" in c.get("/").get_data(as_text=True) and c.get("/static/security.js").status_code == 200)
sys.exit(0 if ok else 1)
