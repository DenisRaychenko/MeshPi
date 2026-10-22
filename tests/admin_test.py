import base64, os, sys, tempfile, threading, time, types
tmp = tempfile.mkdtemp()
os.environ["MESHPI_DB"] = os.path.join(tmp, "messages.db")
os.environ.pop("MESHPI_PASSWORD", None)
sys.path.insert(0, sys.argv[1])
import app as m
import admin
from meshtastic.protobuf import admin_pb2, channel_pb2, localonly_pb2

m.init_db(m.DB_PATH)
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print("ok  " if cond else "FAIL", name, extra)

LOCAL, REMOTE = 0x100, 0x2B2
REMOTE_ID = f"!{REMOTE:08x}"


class FakeIface:
    """Підставний інтерфейс: локальний вузол із справжніми protobuf-структурами й «віддалений», що відповідає на адмін-пакети."""
    def __init__(self):
        self.sent, self.written, self.nodes_by_num = [], [], {}
        self.script = []                      # відповіді віддаленого по черзі: dict-пакети або None (мовчання)
        self.remote_cfg = localonly_pb2.LocalConfig()
        self.remote_cfg.lora.region, self.remote_cfg.lora.hop_limit = 16, 5
        self.remote_cfg.device.role = 2
        self.remote_cfg.security.private_key = b"x" * 32
        self.remote_mod = localonly_pb2.LocalModuleConfig()
        self.remote_mod.telemetry.device_update_interval = 1800
        self.remote_channels = []
        for k in range(8):
            ch = channel_pb2.Channel(index=k, role=1 if k == 0 else 0)
            ch.settings.name = f"ch{k}" if k < 2 else ""
            ch.settings.psk = b"\x01"
            self.remote_channels.append(ch)
        local = types.SimpleNamespace(nodeNum=LOCAL, localConfig=localonly_pb2.LocalConfig(), moduleConfig=localonly_pb2.LocalModuleConfig(),
                                      channels=[channel_pb2.Channel(index=k, role=1 if k == 0 else 0) for k in range(8)])
        local.localConfig.lora.region = 3
        local.writeConfig = lambda s: self.written.append(("config", s))
        local.writeChannel = lambda k: self.written.append(("channel", k))
        local.setOwner = lambda a, b: self.written.append(("owner", a, b))
        local.setFixedPosition = lambda *a: self.written.append(("pos", *a))
        local._getAdminChannelIndex = lambda: 0
        self.localNode = local
        self.nodesByNum = {LOCAL: {"num": LOCAL, "user": {"id": f"!{LOCAL:08x}", "longName": "Local", "shortName": "LOC"}}}
        self.nodes = {n["user"]["id"]: n for n in self.nodesByNum.values()}

    def getMyNodeInfo(self):
        return self.nodesByNum[LOCAL]

    def _getOrCreateByNum(self, num):
        return self.nodes_by_num.setdefault(num, {})

    def sendData(self, p, num, portNum=None, wantAck=False, wantResponse=False, onResponse=None, onResponseAckPermitted=False,
                 channelIndex=0, pkiEncrypted=False):
        self.sent.append((num, p, onResponseAckPermitted, pkiEncrypted))
        reply = self.script.pop(0) if self.script else self.auto(p)
        if reply is not None and onResponse:
            threading.Timer(0.02, onResponse, args=(reply,)).start()

    # --- віддалений вузол ---
    def admin_pkt(self, adm):
        return {"decoded": {"portnum": "ADMIN_APP", "admin": {"raw": adm}}}

    def routing(self, reason="NONE"):
        return {"decoded": {"portnum": "ROUTING_APP", "routing": {"errorReason": reason}}}

    def auto(self, p):
        kind = p.WhichOneof("payload_variant")
        r = admin_pb2.AdminMessage()
        if kind == "get_config_request":
            section = admin_pb2.AdminMessage.ConfigType.Name(p.get_config_request).replace("_CONFIG", "").lower()
            if section == "sessionkey":
                self.nodes_by_num.setdefault(REMOTE, {})["adminSessionPassKey"] = b"k"
                return self.admin_pkt(r)
            getattr(r.get_config_response, section).CopyFrom(getattr(self.remote_cfg, section))
            return self.admin_pkt(r)
        if kind == "get_module_config_request":
            name = {v: k for k, v in admin.MODULE_SECTIONS.items()}[admin_pb2.AdminMessage.ModuleConfigType.Name(p.get_module_config_request)]
            getattr(r.get_module_config_response, name).CopyFrom(getattr(self.remote_mod, name))
            return self.admin_pkt(r)
        if kind == "get_channel_request":
            r.get_channel_response.CopyFrom(self.remote_channels[p.get_channel_request - 1])
            return self.admin_pkt(r)
        if kind == "get_owner_request":
            r.get_owner_response.long_name, r.get_owner_response.short_name = "Remote node", "RMT"
            return self.admin_pkt(r)
        return self.routing()                 # set_* і дії: підтвердження


fi = FakeIface()
m.use_iface(fi)

# ---- опис полів ----
sch = admin.schema("lora")
names = {f["name"] for f in sch}
check("schema: lora fields with enum options", {"region", "modem_preset", "tx_power", "hop_limit"} <= names
      and "EU_433" in next(f for f in sch if f["name"] == "region")["options"])
check("schema: ints carry their range", next(f for f in sch if f["name"] == "hop_limit")["max"] == 2**32 - 1)
check("schema: private key is never described", "private_key" not in {f["name"] for f in admin.schema("security")})
check("schema: every section builds", all(admin.schema(n) for n in [*admin.CONFIG_SECTIONS, *admin.MODULE_SECTIONS]))
check("schema: repeated and nested fields", any(f["repeated"] for f in admin.schema("security")) and any(f.get("fields") for f in admin.schema("mqtt")))

# ---- свій вузол ----
v = admin.read_section(fi, "local", "lora")
check("local read gives names for enums", v["region"] == "EU_868" or v["region"] == "EU_433" or isinstance(v["region"], str), str(v.get("region")))
admin.write_section(fi, "local", "lora", {**v, "hop_limit": 6})
check("local write sets the value and calls writeConfig", fi.localNode.localConfig.lora.hop_limit == 6 and ("config", "lora") in fi.written)
try:
    admin.write_section(fi, "local", "lora", {"hop_limit": "many"})
    check("bad value rejected", False)
except admin.AdminError as e:
    check("bad value rejected", "Некоректне" in str(e), str(e))
try:
    admin.write_section(fi, "local", "lora", {"no_such": 1})
    check("unknown field rejected", False)
except admin.AdminError:
    check("unknown field rejected", True)
try:
    admin.write_section(fi, "local", "security", {})
    check("security is read-only", False)
except admin.AdminError:
    check("security is read-only", True)
fi.localNode.localConfig.security.private_key = b"s" * 32
check("security read hides the private key", "private_key" not in admin.read_section(fi, "local", "security"))

# ---- віддалений вузол ----
admin.ensure_session(fi, REMOTE_ID, 5)
v = admin.read_section(fi, REMOTE_ID, "lora", timeout=5)
num, p, ackok, pki = fi.sent[-1]
check("remote read: admin packet to the node, PKC", num == REMOTE and pki and not ackok)
check("remote read: values parsed", v["region"] == "MY_433" and v["hop_limit"] == 5, str(v))
check("remote module read", admin.read_section(fi, REMOTE_ID, "telemetry", timeout=5)["device_update_interval"] == 1800)
check("remote security read hides private key", "private_key" not in admin.read_section(fi, REMOTE_ID, "security", timeout=5))
admin.write_section(fi, REMOTE_ID, "lora", {**v, "hop_limit": 4}, timeout=5)
num, p, ackok, pki = fi.sent[-1]
check("remote write: set_config with ack accepted", ackok and p.WhichOneof("payload_variant") == "set_config" and p.set_config.lora.hop_limit == 4)
check("remote write sent the session key", p.session_passkey == b"k")
fi.script = [fi.routing("ADMIN_PUBLIC_KEY_UNAUTHORIZED")]
try:
    admin.read_section(fi, REMOTE_ID, "device", timeout=5)
    check("NAK is explained", False)
except admin.AdminError as e:
    check("NAK is explained in plain words", "admin_key" in str(e), str(e))
fi.script = [None]
t0 = time.time()
try:
    admin.read_section(fi, REMOTE_ID, "device", timeout=0.3)
    check("silence times out", False)
except admin.AdminError as e:
    check("silence times out with advice", time.time() - t0 < 2 and "не відповів" in str(e), str(e))
n_before = len(fi.sent)
fi.script = [fi.routing("ADMIN_BAD_SESSION_KEY")]
admin.read_section(fi, REMOTE_ID, "device", timeout=5)
check("bad session key: new key requested and the request repeated", len(fi.sent) - n_before >= 3, str(len(fi.sent) - n_before))

# ---- канали ----
chs = admin.read_channels(fi, REMOTE_ID, timeout=5)
check("remote channels: 8 read in order", [c["index"] for c in chs] == list(range(8)) and chs[0]["role"] == "PRIMARY" and chs[1]["name"] == "ch1")
check("local channels read from the cache", len(admin.read_channels(fi, "local")) == 8)
good = {"index": 2, "role": "SECONDARY", "name": "Dacha", "psk": base64.b64encode(b"k" * 16).decode(), "uplink_enabled": False,
        "downlink_enabled": False, "position_precision": 13}
admin.write_channel(fi, REMOTE_ID, good, timeout=5)
num, p, ackok, pki = fi.sent[-1]
check("remote channel write", p.set_channel.index == 2 and p.set_channel.settings.name == "Dacha"
      and p.set_channel.settings.module_settings.position_precision == 13 and ackok)
admin.write_channel(fi, "local", good)
check("local channel write uses the library", ("channel", 2) in fi.written and fi.localNode.channels[2].settings.name == "Dacha")
for bad, why in (({**good, "psk": "!!!"}, "psk not base64"), ({**good, "psk": base64.b64encode(b"123").decode()}, "psk of 3 bytes"),
                 ({**good, "name": "x" * 12}, "name over 11 bytes"), ({**good, "index": 9}, "index 9")):
    try:
        admin.write_channel(fi, REMOTE_ID, bad, timeout=5)
        check(f"channel rejected: {why}", False)
    except admin.AdminError:
        check(f"channel rejected: {why}", True)

# ---- назва, позиція, дії ----
check("remote owner read", admin.read_owner(fi, REMOTE_ID, 5)["long_name"] == "Remote node")
admin.set_owner(fi, "local", "My node", "MYN")
check("local set_owner", ("owner", "My node", "MYN") in fi.written)
for ln, sn in (("", "A"), ("x" * 40, "A"), ("ok", "TOOLONG"), (5, "A")):
    try:
        admin.set_owner(fi, REMOTE_ID, ln, sn, 5)
        check(f"owner rejected: {ln!r}/{sn!r}", False)
    except admin.AdminError:
        check(f"owner rejected: {str(ln)[:6]!r}/{sn!r}", True)
admin.set_fixed_position(fi, REMOTE_ID, 49.2, 31.4, 120, 5)
p = fi.sent[-1][1]
check("remote fixed position in 1e-7 degrees", p.set_fixed_position.latitude_i == 492000000 and p.set_fixed_position.altitude == 120)
admin.action(fi, REMOTE_ID, "reboot", {"secs": 5}, 5)
check("remote reboot", fi.sent[-1][1].reboot_seconds == 5)
admin.action(fi, REMOTE_ID, "favorite", {"node": "!0000abcd"}, 5)
check("favorite node", fi.sent[-1][1].set_favorite_node == 0xabcd)
for args in ({"secs": 0}, {"secs": "5"}):
    try:
        admin.action(fi, REMOTE_ID, "reboot", args, 5)
        check(f"reboot delay validated {args}", False)
    except admin.AdminError:
        check(f"reboot delay validated {args}", True)

# ---- HTTP: завдання ----
c = m.app.test_client()
sc = c.get("/api/admin/schema").get_json()
check("/api/admin/schema lists sections and fields", "lora" in sc["schemas"] and "mqtt" in sc["sections"]["module"] and "security" in sc["sections"]["readonly"])


def run(**body):
    r = c.post("/api/admin/job", json=body)
    if r.status_code != 200:
        return r.status_code, r.get_json()
    jid = r.get_json()["job"]
    for _ in range(300):
        j = c.get("/api/admin/job?id=" + jid).get_json()
        if j["state"] != "running":
            return 200, j
        time.sleep(0.05)
    return 200, j


code, j = run(op="read", target=REMOTE_ID, section="lora")
check("job: remote read", code == 200 and j["state"] == "done" and j["result"]["values"]["region"] == "MY_433", str(j.get("error")))
code, j = run(op="read", target="local", section="device")
check("job: local read", j["state"] == "done" and "role" in j["result"]["values"])
code, j = run(op="write", target="local", section="lora", values={"hop_limit": 5})
check("job: local write", j["state"] == "done" and fi.localNode.localConfig.lora.hop_limit == 5)
code, j = run(op="channels", target=REMOTE_ID)
check("job: channels with progress", j["state"] == "done" and len(j["result"]["channels"]) == 8)
fi.script = [fi.routing("NOT_AUTHORIZED")]
code, j = run(op="read", target=REMOTE_ID, section="lora")
check("job: a NAK becomes an error message", j["state"] == "error" and "не дозволяє" in j["error"], str(j.get("error")))
check("job: unknown op / target / section rejected",
      run(op="boom", target="local")[0] == 400 and run(op="read", target="x", section="lora")[0] == 400
      and run(op="read", target="local", section="nope")[0] == 400)
check("job: security write rejected", run(op="write", target="local", section="security", values={})[0] == 400)
check("job: destructive action needs confirm", run(op="action", target=REMOTE_ID, name="factory_reset_config")[0] == 400
      and run(op="action", target=REMOTE_ID, name="shutdown")[0] == 400)
code, j = run(op="action", target=REMOTE_ID, name="factory_reset_config", confirm=True)
check("job: confirmed destructive action goes through", j["state"] == "done" and fi.sent[-1][1].factory_reset_config == 1)
code, j = run(op="action", target=REMOTE_ID, name="reboot")
check("job: reboot without confirm is allowed", j["state"] == "done")

fi.script = [None]
admin.call.__defaults__  # noqa: B018
m.admin.call = lambda *a, **k: (time.sleep(0.4), {"decoded": {"admin": {"raw": admin_pb2.AdminMessage()}}})[1]
r1 = c.post("/api/admin/job", json={"op": "owner", "target": REMOTE_ID})
r2 = c.post("/api/admin/job", json={"op": "owner", "target": REMOTE_ID})
check("only one admin operation per connection at a time", r1.status_code == 200 and r2.status_code == 409, str(r2.status_code))
time.sleep(0.8)
check("unknown job id", c.get("/api/admin/job?id=zzz").status_code == 404)
sys.exit(0 if ok else 1)
