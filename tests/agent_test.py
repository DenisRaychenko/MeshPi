import importlib.util, json, os, sys, tempfile, threading, time, types, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
tmp = tempfile.mkdtemp()
os.environ["MESHPI_DB"] = os.path.join(tmp, "messages.db")
os.environ.pop("MESHPI_PASSWORD", None)
TOKEN = "t0ken-" + "x" * 20
os.environ.update(AGENT_TOKEN=TOKEN, AGENT_PORT="0", AGENT_ALLOW="")
cfgdir = os.path.join(tmp, "config.d"); os.makedirs(cfgdir)
os.environ["AGENT_CFG_GLOB"] = os.path.join(cfgdir, "*.yaml")

here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("agent", os.path.join(here, "..", "pi-agent", "mesh-radio-agent.py"))
agent = importlib.util.module_from_spec(spec); spec.loader.exec_module(agent)
sys.path.insert(0, sys.argv[1])
import app as m

ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print("ok  " if cond else "FAIL", name, extra)

# ---- агент ----
calls = []
JOURNAL = "Final Tx power: 8\nSet radio: region=MY_433 name=LongFast power=20\nFinal Tx power: 14\nSet radio: power=10\n"  # найновіші першими
agent.journal_text = lambda: (calls.append(1), JOURNAL)[1]
check("no config files at all: no chip cap is invented", agent.chip_cap() == (None, None))
main_cfg = os.path.join(tmp, "config.yaml"); open(main_cfg, "w").write("Lora:\n  Module: sx1268\n")
agent.CFG_GLOB = main_cfg + os.pathsep + os.environ["AGENT_CFG_GLOB"]
check("the main config.yaml counts too (a Pi without files in config.d): the usual 22", agent.chip_cap() == (22, None))
os.remove(main_cfg); agent.CFG_GLOB = os.environ["AGENT_CFG_GLOB"]
open(os.path.join(cfgdir, "a.yaml"), "w").write("Lora:\n  Module: sx1262\n")
check("config without the key: the usual 22", agent.chip_cap() == (22, None))
open(os.path.join(cfgdir, "lora-e32-400m30s.yaml"), "w").write("Lora:\n  Module: RF95\n  RF95_MAX_POWER: 8\n")
check("RF95_MAX_POWER is found with the name of its config", agent.chip_cap() == (8, "lora-e32-400m30s.yaml"))

# запам'ятовування: рядок «Set radio» зникає з журналу в пам'яті за кілька годин, а значення має лишитись, доки радіо не перезапущено
agent.STATE = os.path.join(tmp, "state.json"); agent._state = {}
keys = {"k": "boot1:100"}
agent.start_key = lambda: keys["k"]
agent.journal_text = lambda: "Final Tx power: 8\nSet radio: power=20\n"
check("fresh journal: values read and not marked as cached", agent.refresh() is True and agent._state["logged"] == 20 and agent._state["final"] == 8)
agent.journal_text = lambda: ""                      # рядки стерлись із журналу
agent.refresh()
check("lines gone from the journal, same start: the last seen values stay", agent._state["logged"] == 20 and agent._state["final"] == 8)
agent._state = {}; agent.load_state()
check("they survive an agent restart (state file)", agent._state.get("logged") == 20 and agent._state.get("key") == "boot1:100")
keys["k"] = "boot1:999"; agent.refresh()
check("meshtasticd restarted and the journal has no lines yet: the old values are dropped", agent._state["logged"] is None and agent._state["final"] is None)
agent.journal_text = lambda: "Set radio: power=12\n"; agent.refresh()
check("new start, new line: the new value", agent._state["logged"] == 12 and agent._state["key"] == "boot1:999")
keys["k"] = None; agent.journal_text = lambda: ""; agent.refresh()
check("start key unknown (systemctl unavailable): the cache is kept, not wiped", agent._state["logged"] == 12)
agent._state = {}; agent.STATE = os.path.join(tmp, "missing-dir", "state.json"); agent.refresh()
agent._cache = (0.0, {}); check("no writable state directory: still answers from memory", "logged" in agent.radio_info())
agent._state = {}; agent.STATE = os.path.join(tmp, "state2.json")
keys["k"] = "b:1"; agent.journal_text = lambda: (calls.append(1), JOURNAL)[1]; agent._cache = (0.0, {})

srv = ThreadingHTTPServer(("127.0.0.1", 0), agent.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"

def get(url, token=TOKEN, method="GET"):
    req = urllib.request.Request(url, method=method, headers={"Authorization": "Bearer " + token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

check("/ping answers without a token and without data", get(base + "/ping", None) == (200, "ok"))
check("no token: 401", get(base + "/radio.json", None)[0] == 401)
check("wrong token: 401", get(base + "/radio.json", "wrong-token-wrong-token")[0] == 401)
code, body = get(base + "/radio.json")
d = json.loads(body)
check("right token: the four fields", code == 200 and d["logged"] == 20 and d["final"] == 8 and d["cap"] == 8 and d["radio"] == "lora-e32-400m30s.yaml", body)
get(base + "/radio.json")
check("journal is read once within the cache window", len(calls) == 1, str(len(calls)))
check("only GET: POST is 405", get(base + "/radio.json", method="POST")[0] == 405)
check("unknown path with a valid token: 404", get(base + "/etc/passwd")[0] == 404)
agent.ALLOW = {"10.9.9.9"}
check("client not in the allow list: 403", get(base + "/radio.json")[0] == 403)
agent.ALLOW = set()

# ---- панель ↔ агент ----
fi = types.SimpleNamespace(localNode=types.SimpleNamespace(localConfig=types.SimpleNamespace(lora=types.SimpleNamespace(tx_power=10))),
                           nodesByNum={}, nodes={}, getMyNodeInfo=lambda: {"user": {"id": "!1"}})
m.use_iface(fi)
c = m.app.test_client()
r = c.get("/api/power").get_json()
check("without agent and outside the Pi: no invented chip cap, source unknown", r["cap"] is None and r["effective"] is None and r["source"] is None
      and r["patched"] is None and r["setting"] == 10, str({k: r[k] for k in ("cap", "effective", "source", "patched")}))
check("no journal data, node setting known: an estimate, clearly flagged, without the region limit", r["estimate"] is True and r["logged"] == 10 and r["setting"] == 10)
bad = {"action": "update", "id": "main", "name": "Основний", "host": "localhost", "port": 4404}
check("agent url must be http(s)://host[:port]", all(c.post("/api/conns", json={**bad, "agent": u, "agent_token": TOKEN}).status_code == 400
      for u in ("ftp://x", "http://user:pw@x", "http://x/path", "http://x?y=1", "http://x:99999", "javascript:1", "x")))
check("agent without a token is refused", c.post("/api/conns", json={**bad, "agent": base}).status_code == 400)
check("short token refused", c.post("/api/conns", json={**bad, "agent": base, "agent_token": "short"}).status_code == 400)
j = c.post("/api/conns", json={**bad, "agent": base + "/", "agent_token": TOKEN}).get_json()
main = next(x for x in j["conns"] if x["id"] == "main")
check("agent saved (trailing slash dropped); the token never goes to the browser",
      main["agent"] == base and main["agent_token_set"] is True and TOKEN not in json.dumps(j) and TOKEN not in c.get("/api/conns").get_data(as_text=True))
check("nodes.json keeps the token for the next start", json.load(open(m.NODES_FILE))[0]["agent_token"] == TOKEN)
r = c.get("/api/power").get_json()
check("/api/power takes the four values from the agent (not an estimate)", r["estimate"] is False and r["source"] == "agent" and r["logged"] == 20 and r["effective"] == 8 and r["cap"] == 8
      and r["radio"] == "lora-e32-400m30s.yaml" and r["agent_error"] is None, str(r))
j = c.post("/api/conns", json={**bad}).get_json()
check("editing without agent fields keeps the agent and its token", next(x for x in j["conns"] if x["id"] == "main")["agent"] == base
      and json.load(open(m.NODES_FILE))[0]["agent_token"] == TOKEN)
j = c.post("/api/conns", json={**bad, "agent": base, "agent_token": "another-token-another-token"}).get_json()
r = c.get("/api/power").get_json()
check("wrong token: a plain explanation instead of numbers", r["agent_error"] == "токен не підходить" and r["effective"] is None, str(r["agent_error"]))
agent.ALLOW = {"10.9.9.9"}; m.conns["main"].agent_cache = (0.0, None)
check("IP not allowed on the agent: explained", c.get("/api/power").get_json()["agent_error"] == "IP сервера панелі не дозволено на агенті")
agent.ALLOW = set()
c.post("/api/conns", json={**bad, "agent": base, "agent_token": TOKEN})
srv.shutdown(); srv.server_close(); m.conns["main"].agent_cache = (0.0, None)
check("agent down: explained, the page does not fail", c.get("/api/power").status_code == 200 and "немає зв'язку з агентом" in c.get("/api/power").get_json()["agent_error"])

class Redirect(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302); self.send_header("Location", "http://127.0.0.1:1/steal"); self.end_headers()
    def log_message(self, *a): pass
rs = ThreadingHTTPServer(("127.0.0.1", 0), Redirect); threading.Thread(target=rs.serve_forever, daemon=True).start()
c.post("/api/conns", json={**bad, "agent": f"http://127.0.0.1:{rs.server_address[1]}", "agent_token": TOKEN}); m.conns["main"].agent_cache = (0.0, None)
check("redirects from the agent are not followed", c.get("/api/power").get_json()["agent_error"] == "відповідь HTTP 302")
rs.shutdown()
j = c.post("/api/conns", json={**bad, "agent": ""}).get_json()
main = next(x for x in j["conns"] if x["id"] == "main")
check("an empty address switches the agent off and erases the token", main["agent"] == "" and main["agent_token_set"] is False
      and json.load(open(m.NODES_FILE))[0]["agent_token"] == "")
check("a new node can be added with its own agent", c.post("/api/conns", json={"action": "add", "name": "Дача", "host": "10.0.0.2", "port": 4403,
      "agent": "http://10.0.0.2:8097", "agent_token": TOKEN}).status_code == 200)
sys.exit(0 if ok else 1)
