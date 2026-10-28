import json, os, sys, tempfile, threading, time, types
tmp = tempfile.mkdtemp()
os.environ["MESHPI_DB"] = os.path.join(tmp, "messages.db")
os.environ["MESHPI_NODES_FILE"] = os.path.join(tmp, "nodes.json")
os.environ["MESHPI_NOTIFY_FILE"] = os.path.join(tmp, "notify.json")
os.environ["MESHPI_AUTO_FILE"] = os.path.join(tmp, "auto.json")
os.environ.pop("MESHPI_PASSWORD", None)
sys.path.insert(0, sys.argv[1])
import app as m
import extras as x

m.init_db(m.DB_PATH)
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print("ok  " if cond else "FAIL", name, extra)

T = 1_800_000_000
D = x.auto_defaults()
check("defaults: everything off, DM only", not D["reply"]["on"] and not D["announce"]["on"] and D["reply"]["scope"] == "dm")


def cfg(**kw):
    c = x.auto_update(D, {"reply": {"on": True, **kw}})[0]
    return c


MSG = {"text": "info", "sender": "!000000aa", "sender_name": "Alpha", "dm": True, "mqtt": False, "own": False, "t": T, "hops": 1, "snr": 6.2, "rssi": -90}
CTX = {"chan": 5, "local_ids": {"!00000100"}, "nodes": 7, "bat": "80%", "time": "12:00", "temp": 21.5}
text, why = x.auto_reply(D, {}, MSG, CTX, T)
check("off by default: no reply", text is None and why == "вимкнено")
st = {}
text, why = x.auto_reply(cfg(), st, MSG, CTX, T)
check("a DM 'info' gets the templated reply with the bot prefix", text and text.startswith("[авто] ") and "1 стрибків" in text and "SNR 6" in text and "7" in text, str(text))
check("... and the second one from the same node is paused", x.auto_reply(cfg(), st, MSG, CTX, T + 10) == (None, "пауза для цього вузла"))
check("... until the cooldown has passed", x.auto_reply(cfg(), st, {**MSG, "t": T + 400}, CTX, T + 400)[0] is not None)
check("a channel message is ignored in the default DM-only scope", x.auto_reply(cfg(), {}, {**MSG, "dm": False}, CTX, T)[1] == "інша область")
check("channel scope answers channel messages and not DMs", x.auto_reply(cfg(scope="channel"), {}, {**MSG, "dm": False}, CTX, T)[0] is not None
      and x.auto_reply(cfg(scope="channel"), {}, MSG, CTX, T)[1] == "інша область")
check("both scopes", all(x.auto_reply(cfg(scope="both"), {}, {**MSG, "dm": d}, CTX, T)[0] for d in (True, False)))
check("the trigger word is case-insensitive and ignores punctuation and the rest of the text", x.auto_reply(cfg(), {}, {**MSG, "text": "  INFO?! будь ласка"}, CTX, T)[0])
check("an unknown word gets nothing", x.auto_reply(cfg(), {}, {**MSG, "text": "привіт"}, CTX, T)[1] == "немає правила")
check("our own messages, MQTT copies and nameless senders are skipped", all(x.auto_reply(cfg(), {}, {**MSG, **k}, CTX, T)[0] is None
      for k in ({"own": True}, {"mqtt": True}, {"sender": None})))
check("the node of this panel (another connection) is never answered", x.auto_reply(cfg(), {}, {**MSG, "sender": "!00000100"}, CTX, T)[1] == "вузол цієї ж панелі")
check("the bot never answers a bot reply (loop protection)", x.auto_reply(cfg(), {}, {**MSG, "text": "[авто] info"}, CTX, T)[1] == "це відповідь автовідповідача")
check("an old message (the proxy replayed it) is not answered", x.auto_reply(cfg(), {}, {**MSG, "t": T - 500}, CTX, T)[1] == "повідомлення застаріло")
check("a busy channel stops replies", x.auto_reply(cfg(), {}, MSG, {**CTX, "chan": 30}, T)[1] == "ефір зайнятий"
      and x.auto_reply(cfg(chan_cutoff=0), {}, MSG, {**CTX, "chan": 90}, T)[0] is not None)
st = {}
c = cfg(per_hour=2, cooldown=60)
got = [x.auto_reply(c, st, {**MSG, "sender": f"!{k:08x}"}, CTX, T + k)[0] for k in range(4)]
check("the hourly budget caps replies across all nodes", [bool(g) for g in got] == [True, True, False, False], str(got))
check("... and frees up after an hour", x.auto_reply(c, st, {**MSG, "sender": "!000000ff", "t": T + 3700}, CTX, T + 3700)[0] is not None)
check("placeholders: unknown ones stay, missing values become ?", x.auto_render("{name} {hops} {nope} {temp}", {"name": "A", "hops": None, "temp": 3}) == "A ? {nope} 3")
check("a placeholder cannot run code", x.auto_render("{0.__class__}", {}) == "{0.__class__}")
check("the reply never exceeds 200 bytes", len(x.auto_render("я" * 150 + "{name}", {"name": "й" * 80}).encode()) <= 200)

# ---- валідація ----
bad = lambda body: x.auto_update(D, body)[1]
check("bad scope / ranges are refused", bad({"reply": {"scope": "x"}}) and bad({"reply": {"cooldown": 5}}) and bad({"reply": {"per_hour": 99}}) and bad({"reply": {"chan_cutoff": 101}}))
check("a rule needs a one-word trigger and a short text", bad({"reply": {"rules": [{"word": "two words", "text": "x"}]}}) and bad({"reply": {"rules": [{"word": "a", "text": ""}]}})
      and bad({"reply": {"rules": [{"word": "a", "text": "я" * 70}]}}) and bad({"reply": {"rules": [{"word": "a", "text": "x"}, {"word": "A", "text": "y"}]}}))
check("at most 10 rules and 5 announcements", bad({"reply": {"rules": [{"word": f"w{k}", "text": "x"} for k in range(11)]}})
      and bad({"announce": {"items": [{"text": "x"}] * 6}}))
check("an announcement every <60 min is refused", bad({"announce": {"items": [{"text": "x", "every": 30}]}}))
check("duplicate ids are refused", bad({"announce": {"items": [{"id": 1, "text": "x"}, {"id": 1, "text": "y"}]}}))
check("floats, strings and booleans are not accepted as numbers", bad({"reply": {"cooldown": 60.5}}) and bad({"reply": {"cooldown": "300"}}) and bad({"reply": {"per_hour": True}}))
n, w = x.auto_update(D, {"reply": {"on": True, "rules": [{"word": "Погода", "text": "{temp}°C"}]}, "announce": {"items": [{"text": "Привіт з Meshpi", "every": 120, "ch": 1}]}})
check("a valid update lowers the word's case and keeps the rest", w is None and n["reply"]["rules"] == [{"word": "погода", "text": "{temp}°C"}] and n["announce"]["items"][0]["every"] == 120 and D["reply"]["rules"][0]["word"] == "info")

# ---- заплановані оголошення ----
A = x.auto_update(D, {"announce": {"on": True, "items": [{"id": 1, "text": "CQ з {nodes} вузлів", "every": 60, "ch": 0, "quiet_from": 23, "quiet_to": 6}]}})[0]
st = {}
noon = time.mktime((2026, 10, 24, 12, 0, 0, 0, 0, -1))
check("the first call only starts the clock", x.auto_due(A, st, CTX, noon) == [])
check("before the interval: nothing", x.auto_due(A, st, CTX, noon + 1800) == [])
due = x.auto_due(A, st, CTX, noon + 3601)
check("after the interval: one announcement, rendered, prefixed", len(due) == 1 and due[0][1] == "[авто] CQ з 7 вузлів", str(due))
check("... and then the clock restarts", x.auto_due(A, st, CTX, noon + 3700) == [])
night = time.mktime((2026, 10, 24, 23, 30, 0, 0, 0, -1))
st = {"ann": {"1": night - 7200}}
check("quiet hours (23-06, across midnight) hold it back", x.auto_due(A, st, CTX, night) == [])
check("quiet hours logic", x.in_quiet(23, 23, 6) and x.in_quiet(2, 23, 6) and not x.in_quiet(6, 23, 6) and not x.in_quiet(12, 23, 6)
      and x.in_quiet(10, 9, 17) and not x.in_quiet(17, 9, 17) and not x.in_quiet(3, None, None) and not x.in_quiet(3, 5, 5))
st = {"ann": {"1": noon - 7200}}
check("a busy channel holds announcements back", x.auto_due(A, st, {**CTX, "chan": 40}, noon) == [])
st = {"ann": {"1": noon - 7200}, "sent": [noon - 10] * 6}
check("the shared hourly budget holds them back too", x.auto_due(A, st, CTX, noon) == [])
check("disabled or empty: nothing", x.auto_due(D, {}, CTX, noon) == [])

# ---- застосунок ----
c = m.app.test_client()
j = c.get("/api/auto").get_json()
check("API: defaults, limits and placeholders", j["cfg"]["reply"]["on"] is False and "temp" in j["placeholders"] and j["limits"]["every_min"] == 60 and j["sent_last_hour"] == 0)
r = c.post("/api/auto", json={"reply": {"on": True, "cooldown": 5}})
check("API: a bad value is a 400 and changes nothing", r.status_code == 400 and not m.auto_cfg["reply"]["on"])
r = c.post("/api/auto", json={"reply": {"on": True, "scope": "both", "cooldown": 60, "rules": [{"word": "pong2", "text": "відповідь {name}"}]}})
check("API: a valid update is saved and returned", r.status_code == 200 and r.get_json()["cfg"]["reply"]["on"] and json.load(open(os.environ["MESHPI_AUTO_FILE"]))["reply"]["scope"] == "both")
check("... and survives a restart (reload from the file)", m.auto_load()["reply"]["rules"][0]["word"] == "pong2")
open(os.environ["MESHPI_AUTO_FILE"], "w").write("{broken")
check("a corrupt file gives the safe defaults", m.auto_load()["reply"]["on"] is False)


class Fake:
    def __init__(self):
        self.sent = []
        self.nodesByNum = {0x100: {"num": 0x100, "user": {"id": "!00000100", "longName": "Me"}, "deviceMetrics": {"batteryLevel": 77}},
                           0xAA: {"num": 0xAA, "user": {"id": "!000000aa", "longName": "Alpha"}, "lastHeard": int(time.time()) - 60}}
        self.nodes = {"!000000aa": self.nodesByNum[0xAA]}

    def getMyNodeInfo(self):
        return self.nodesByNum[0x100]

    def sendText(self, text, destinationId="^all", channelIndex=0, replyId=None, **kw):
        self.sent.append((text, destinationId, channelIndex))
        return types.SimpleNamespace(id=4242 + len(self.sent))


fi = Fake()
m.use_iface(fi)
m.auto_cfg, _ = x.auto_update(x.auto_defaults(), {"reply": {"on": True, "scope": "both", "rules": [{"word": "тест", "text": "Привіт, {name}! вузлів {nodes}, заряд {bat}"}]}})
m.auto_state.clear()
pkt = {"from": 0xAA, "fromId": "!000000aa", "to": 0x100, "toId": "!00000100", "id": 77, "channel": 0, "rxTime": int(time.time()), "rxSnr": 4.0, "rxRssi": -95,
       "hopStart": 3, "hopLimit": 2, "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "тест"}}
m.on_text(pkt, fi)
time.sleep(0.5)
check("on_text: a DM trigger is answered in a DM from a worker thread", len(fi.sent) == 1 and fi.sent[0][1] == "!000000aa" and fi.sent[0][0].startswith("[авто] Привіт, Alpha!")
      and "заряд 77%" in fi.sent[0][0], str(fi.sent))
check("the reply is stored in the chat as ours and journalled as 'система'", m.db.execute("SELECT COUNT(*) FROM msg WHERE out = 1 AND text LIKE '[авто]%'").fetchone()[0] == 1
      and m.db.execute("SELECT COUNT(*) FROM audit WHERE role = 'система'").fetchone()[0] == 1)
check("the sent time is persisted (limits survive a restart)", json.load(open(m.AUTO_STATE_FILE))["sent"])
m.on_text({**pkt, "id": 78}, fi)
time.sleep(0.3)
check("a second trigger within the cooldown sends nothing", len(fi.sent) == 1)
m.auto_state.clear()
m.on_text({**pkt, "id": 79, "toId": "^all", "to": 0xFFFFFFFF, "channel": 2}, fi)
time.sleep(0.4)
check("a channel trigger is answered in that channel to everyone", len(fi.sent) == 2 and fi.sent[1][1] == "^all" and fi.sent[1][2] == 2, str(fi.sent[-1:]))
m.auto_state.clear()
m.on_text({**pkt, "id": 80, "fromId": "!00000100", "from": 0x100}, fi)
time.sleep(0.3)
check("our own message is never answered", len(fi.sent) == 2)
m.auto_cfg["reply"]["on"] = False
m.auto_state.clear()
m.on_text({**pkt, "id": 81}, fi)
time.sleep(0.3)
check("switched off: silent", len(fi.sent) == 2)
open(m.PONGFILE, "w").write("")
m.auto_cfg, _ = x.auto_update(x.auto_defaults(), {"reply": {"on": True, "rules": [{"word": "ping", "text": "мій pong"}]}})
m.auto_state.clear()
before = len(fi.sent)
m.on_text({**pkt, "id": 82, "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "ping"}}, fi)
time.sleep(0.5)
check("with the simple PONG enabled, a 'ping' rule does not double-answer", all("мій pong" not in s[0] for s in fi.sent[before:]))
os.remove(m.PONGFILE)

# планувальник
m.auto_cfg, _ = x.auto_update(x.auto_defaults(), {"announce": {"on": True, "items": [{"id": 1, "text": "CQ", "every": 60, "ch": 1}]}})
m.auto_state.clear()
conn = m.conns["main"]
n0 = len(fi.sent)
m.auto_tick(conn, time.time())
check("the scheduler starts its clock first and sends nothing", len(fi.sent) == n0)
m.auto_tick(conn, time.time() + 3700)
check("later it broadcasts on the chosen channel", len(fi.sent) == n0 + 1 and fi.sent[-1] == ("[авто] CQ", "^all", 1), str(fi.sent[-1]))
conn.iface = None
m.auto_state["ann"]["1"] = 0
m.auto_tick(conn, time.time() + 9000)
check("offline: nothing is sent", len(fi.sent) == n0 + 1)
check("the page has the automation section and script", "autobox" in c.get("/").get_data(as_text=True) and c.get("/static/auto.js").status_code == 200)
sys.exit(0 if ok else 1)
