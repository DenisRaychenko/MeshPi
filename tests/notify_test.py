import json, os, sys, tempfile, threading, time, types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
tmp = tempfile.mkdtemp()
os.environ["MESHPI_DB"] = os.path.join(tmp, "messages.db")
os.environ["MESHPI_NODES_FILE"] = os.path.join(tmp, "nodes.json")
os.environ["MESHPI_NOTIFY_FILE"] = os.path.join(tmp, "notify.json")
os.environ.pop("MESHPI_PASSWORD", None)
sys.path.insert(0, sys.argv[1])
import app as m
import extras as x
import notify as n

m.init_db(m.DB_PATH)
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print("ok  " if cond else "FAIL", name, extra)

T = 1_800_000_000
R = {"battery": 20, "chan": 40}


def snap(online=True, battery=80, uptime=1000, chan=(10, 10, 10), favs=()):
    return {"online": online, "me": {"id": "!00000100", "name": "Me", "battery": battery, "uptime": uptime}, "favs": list(favs), "chan": list(chan)}


# ---- порогові тривоги ----
st = {}
check("a healthy node gives no alerts", x.alerts(st, snap(), R, T) == [])
check("battery under the threshold: one alert", [k for k, *_ in x.alerts(st, snap(battery=15), R, T + 10)] == ["bat_low"])
check("... and not again while it stays low", x.alerts(st, snap(battery=12), R, T + 20) == [])
check("... nor while it recovers a little (hysteresis)", x.alerts(st, snap(battery=25), R, T + 30) == [])
x.alerts(st, snap(battery=40), R, T + 40)
check("after a real recovery it can alert again", [k for k, *_ in x.alerts(st, snap(battery=10), R, T + 50)] == ["bat_low"])
check("101 % means mains power: no alert", x.alerts({}, snap(battery=101), R, T) == [])
check("threshold 0 switches the battery alert off", x.alerts({}, snap(battery=1), {"battery": 0, "chan": 40}, T) == [])
fav = {"id": "!00000200", "name": "Гора", "battery": 5}
ev = x.alerts({}, snap(favs=[fav]), R, T)
check("a favourite with a low battery alerts too, with its name", len(ev) == 1 and ev[0][1] == "!00000200" and "Гора" in ev[0][2], str(ev))
st = {}
check("channel busy only twice: nothing yet", x.alerts(st, snap(chan=(50, 50)), R, T) == [])
check("three busy samples in a row: alert", [k for k, *_ in x.alerts(st, snap(chan=(45, 50, 55)), R, T + 10)] == ["chan_high"])
check("... once", x.alerts(st, snap(chan=(50, 50, 50)), R, T + 20) == [])
x.alerts(st, snap(chan=(10, 10, 10)), R, T + 30)
check("it re-arms after the channel calms down", [k for k, *_ in x.alerts(st, snap(chan=(60, 60, 60)), R, T + 40)] == ["chan_high"])
st = {}
x.alerts(st, snap(uptime=100000), R, T)
ev = x.alerts(st, snap(uptime=30), R, T + 60)
check("uptime dropping means a reboot", [k for k, *_ in ev] == ["reboot"] and "перезапустився" in ev[0][2], str(ev))
check("normal uptime growth is not a reboot", x.alerts(st, snap(uptime=500), R, T + 120) == [])
st = {}
check("a short disconnect is ignored", x.alerts(st, snap(online=False), R, T) == [] and x.alerts(st, snap(online=False), R, T + 60) == [])
ev = x.alerts(st, snap(online=False), R, T + x.LINK_GAP + 1)
check("a long disconnect: link_lost once", [k for k, *_ in ev] == ["link_lost"] and x.alerts(st, snap(online=False), R, T + 500) == [])
ev = x.alerts(st, snap(online=True), R, T + 700)
check("reconnect: link_ok with the downtime", [k for k, *_ in ev] == ["link_ok"] and "хв" in ev[0][2], str(ev))
check("no alerts from a stale snapshot while offline", x.alerts({}, {"online": False, "me": None, "favs": [], "chan": [90, 90, 90]}, R, T) == [])

# ---- налаштування й токени ----
cfg = n.defaults()
check("defaults: nothing on, favourites and alerts on, new nodes off", not any(cfg[c]["on"] for c in ("telegram", "ntfy", "webhook"))
      and cfg["kinds"]["fav_off"] and cfg["kinds"]["bat_low"] and not cfg["kinds"]["new"])
TOKEN = "123456789:" + "A" * 35
c2, why = n.update(cfg, {"telegram": {"on": True, "token": TOKEN, "chat": "-1001234"}})
check("a valid Telegram setup is accepted", why is None and c2["telegram"]["on"] and c2["telegram"]["token"] == TOKEN)
check("... and does not touch the original", not cfg["telegram"]["on"])
check("a bad Telegram token is refused", n.update(cfg, {"telegram": {"on": True, "token": "abc", "chat": "1"}})[1])
check("Telegram without a chat is refused", n.update(cfg, {"telegram": {"on": True, "token": TOKEN}})[1])
check("a bad chat id is refused", n.update(cfg, {"telegram": {"chat": "javascript:1"}})[1])
check("ntfy needs a server and a topic", n.update(cfg, {"ntfy": {"on": True, "url": "https://ntfy.sh"}})[1]
      and n.update(cfg, {"ntfy": {"on": True, "url": "https://ntfy.sh", "topic": "t1"}})[1] is None)
check("bad ntfy server / topic refused", n.update(cfg, {"ntfy": {"url": "ftp://x"}})[1] and n.update(cfg, {"ntfy": {"url": "http://user:pw@x"}})[1]
      and n.update(cfg, {"ntfy": {"topic": "a/b"}})[1])
check("webhook url rules", n.update(cfg, {"webhook": {"url": "http://host:8080/hook"}})[1] is None and n.update(cfg, {"webhook": {"url": "file:///etc/passwd"}})[1]
      and n.update(cfg, {"webhook": {"url": "http://x?y=1"}})[1])
check("thresholds are numbers 0..100", n.update(cfg, {"rules": {"battery": 150}})[1] and n.update(cfg, {"rules": {"chan": True}})[1]
      and n.update(cfg, {"rules": {"battery": 30}})[0]["rules"]["battery"] == 30)
check("unknown event kinds are ignored", "evil" not in n.update(cfg, {"kinds": {"evil": True}})[0]["kinds"])
c3, _ = n.update(c2, {"telegram": {"chat": "-1001234", "token": ""}})
check("an empty token keeps the stored one", c3["telegram"]["token"] == TOKEN)
c4, _ = n.update(c2, {"clear": ["telegram"]})
check("clear erases a channel with its token", c4["telegram"] == n.defaults()["telegram"])
pub = json.dumps(n.public(c2))
check("the public view never contains a token", TOKEN not in pub and '"token_set": true' in pub)
path = os.path.join(tmp, "n.json")
n.save(path, c2)
check("saved and loaded back", n.load(path)["telegram"]["token"] == TOKEN)
if os.name == "posix":
    check("the file is private (600)", oct(os.stat(path).st_mode)[-3:] == "600")
open(path, "w").write("{broken")
check("a corrupt file gives defaults", n.load(path) == n.defaults())

# ---- відправлення ----
got = []


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        got.append((self.path, dict(self.headers), body))
        code = 500 if self.path == "/fail" else 302 if self.path == "/redir" else 200
        self.send_response(code)
        if code == 302:
            self.send_header("Location", "http://127.0.0.1:1/x")
        self.end_headers()
    def log_message(self, *a): pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
cfg, why = n.update(n.defaults(), {"ntfy": {"on": True, "url": base, "topic": "meshpi", "token": "tk1"},
                                    "webhook": {"on": True, "url": base + "/hook", "token": "wh1"}})
res = n.send(cfg, "Meshpi · Основний", "★ Гора не на зв'язку", "warn", T)
check("ntfy and webhook both delivered", res == [("ntfy", True, "HTTP 200"), ("webhook", True, "HTTP 200")], str(res))
nt = next(g for g in got if g[0] == "/meshpi")
check("ntfy: UTF-8 body, priority high, bearer token", "Гора" in nt[2] and nt[1].get("Priority") == "high" and nt[1].get("Authorization") == "Bearer tk1")
wh = next(g for g in got if g[0] == "/hook")
j = json.loads(wh[2])
check("webhook: JSON with source, text and level", j["source"] == "meshpi" and "Гора" in j["text"] and j["level"] == "warn" and wh[1].get("Authorization") == "Bearer wh1")
cfg["webhook"]["url"] = base + "/fail"
check("a server error is reported, not raised", n.send(cfg, "t", "x")[1] == ("webhook", False, "HTTP 500"))
cfg["webhook"]["url"] = base + "/redir"
r = n.send(cfg, "t", "x")[1]
check("redirects are not followed", r[1] is False and "302" in r[2], str(r))
cfg["webhook"]["url"] = "http://127.0.0.1:1/x"
check("an unreachable server is reported", n.send(cfg, "t", "x")[1][1] is False)
check("no channels, nothing sent", n.send(n.defaults(), "t", "x") == [])

# ---- API й події ----
c = m.app.test_client()
check("GET /api/notify: defaults without secrets", c.get("/api/notify").get_json()["telegram"]["token_set"] is False)
r = c.post("/api/notify", json={"webhook": {"on": True, "url": base + "/hook2", "token": "secret-wh"}, "kinds": {"fav_off": True}})
check("POST saves and masks", r.status_code == 200 and "secret-wh" not in r.get_data(as_text=True) and r.get_json()["webhook"]["token_set"])
check("... and persists to the file", json.load(open(os.environ["MESHPI_NOTIFY_FILE"]))["webhook"]["token"] == "secret-wh")
check("bad input gives 400 with a reason", c.post("/api/notify", json={"ntfy": {"on": True}}).status_code == 400
      and c.post("/api/notify", data="not json", content_type="text/plain").status_code == 400)
got.clear()
t = c.post("/api/notify/test").get_json()
check("the test endpoint reports per channel", t == [{"channel": "webhook", "ok": True, "why": "HTTP 200"}] and got and got[0][0] == "/hook2", str(t))
m.add_event("main", "fav_off", "!1", "★ Гора не на зв'язку: не чути 35 хв")
time.sleep(0.6)
check("an event of an enabled kind is pushed to the channel", any(g[0] == "/hook2" and "Гора" in json.loads(g[2])["text"] for g in got), str(len(got)))
got.clear()
m.add_event("main", "new", "!2", "Новий вузол X")
time.sleep(0.4)
check("an event of a disabled kind (new nodes) is not pushed", not got)
c.post("/api/notify", json={"clear": ["webhook"]})
check("with no channel left, test says so", c.post("/api/notify/test").status_code == 400)

# ---- alerts_tick записує події ----
class Fake:
    def __init__(self):
        self.nodesByNum = {0x100: {"num": 0x100, "user": {"id": "!00000100", "shortName": "Me"}, "deviceMetrics": {"batteryLevel": 9, "uptimeSeconds": 5000}},
                           0x200: {"num": 0x200, "user": {"id": "!00000200", "shortName": "Fav"}, "isFavorite": True, "deviceMetrics": {"batteryLevel": 8}}}
        self.nodes = {}
    def getMyNodeInfo(self):
        return self.nodesByNum[0x100]


m.use_iface(Fake())
conn = m.conns["main"]
m.alert_state.clear()
m.alerts_tick(conn, T)
evs = c.get("/api/events").get_json()["events"]
check("alerts_tick journals low-battery events for the node and the favourite", sorted(e["node"] for e in evs if e["kind"] == "bat_low") == ["!00000100", "!00000200"], str(evs))
conn.iface = None
m.alerts_tick(conn, T + 10)
m.alerts_tick(conn, T + 10 + x.LINK_GAP + 5)
check("a long outage is journalled as link_lost", any(e["kind"] == "link_lost" for e in c.get("/api/events").get_json()["events"]))
page = c.get("/").get_data(as_text=True)
check("the page has the notification section and script", "notifybox" in page and c.get("/static/notify.js").status_code == 200)
sys.exit(0 if ok else 1)
