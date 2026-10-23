import base64, json, os, sys, tempfile
tmp = tempfile.mkdtemp()
os.environ.update(MESHPI_DB=os.path.join(tmp, "messages.db"), MESHPI_PASSWORD="admin-password-1", MESHPI_USER="admin",
                  MESHPI_NODES_FILE=os.path.join(tmp, "nodes.json"), MESHPI_NOTIFY_FILE=os.path.join(tmp, "notify.json"))
sys.path.insert(0, sys.argv[1])
import app as m

m.init_db(m.DB_PATH)
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print("ok  " if cond else "FAIL", name, extra)

ADMIN_PW, VIEW_PW = "admin-password-1", "viewer-password-2"
admin, viewer, anon = m.app.test_client(), m.app.test_client(), m.app.test_client()
check("admin logs in", admin.post("/login", data={"user": "admin", "password": ADMIN_PW}).status_code == 302)
w = admin.get("/api/whoami").get_json()
check("whoami: admin, viewer role not set up yet", w["role"] == "admin" and w["auth"] and not w["viewer"])
check("the viewer cannot log in before a password exists", viewer.post("/login", data={"user": "viewer", "password": VIEW_PW}).status_code == 401)

# ---- пароль глядача ----
check("too short viewer password refused", admin.post("/api/viewer", json={"password": "short"}).status_code == 400)
check("viewer password equal to the admin's refused", admin.post("/api/viewer", json={"password": ADMIN_PW}).status_code == 400)
r = admin.post("/api/viewer", json={"password": VIEW_PW})
check("admin sets the viewer password", r.status_code == 200 and r.get_json()["viewer_user"] == "viewer")
check("the viewer password is stored hashed, file is private",
      VIEW_PW not in open(m.VIEWER_PWFILE).read() and "$" in open(m.VIEWER_PWFILE).read())
check("wrong viewer password -> 401", viewer.post("/login", data={"user": "viewer", "password": "nope-nope-nope"}).status_code == 401)
check("the admin's password does not open the viewer account", viewer.post("/login", data={"user": "viewer", "password": ADMIN_PW}).status_code == 401)
check("viewer logs in", viewer.post("/login", data={"user": "viewer", "password": VIEW_PW}).status_code == 302)
check("whoami: viewer", viewer.get("/api/whoami").get_json()["role"] == "viewer")
check("the page opens for the viewer", viewer.get("/").status_code == 200)

# ---- що глядач може ----
for path in ("/api/status", "/api/nodes", "/api/messages", "/api/mesh", "/api/events", "/api/netstats", "/api/waypoints", "/api/packets",
             "/api/packets.csv", "/api/history", "/api/conns", "/api/health"):
    code = viewer.get(path).status_code
    check(f"viewer may read {path}", code != 403 and code != 401, str(code))
for path in ("/api/audit", "/api/notify", "/api/security", "/api/channel-qr", "/api/backup", "/api/admin/schema", "/api/power", "/api/log",
             "/api/system", "/api/location", "/api/scan", "/api/ask", "/api/autotrace", "/api/admin/job"):
    check(f"viewer may NOT read {path}", viewer.get(path).status_code == 403)
for path, js in (("/api/send", {"text": "x"}), ("/api/password", {"old": VIEW_PW, "new": "another-pass-3"}), ("/api/viewer", {"clear": True}),
                 ("/api/restart", {}), ("/api/radio", {}), ("/api/notify", {}), ("/api/notify/test", {}), ("/api/restore", {}),
                 ("/api/waypoints", {}), ("/api/admin/job", {}), ("/api/forget", {}), ("/api/autotrace", {}), ("/api/ask", {})):
    check(f"viewer may NOT POST {path}", viewer.post(path, json=js).status_code == 403)
check("viewer may switch the node", viewer.post("/api/conns", json={"action": "select", "id": "main"}).status_code == 200)
check("viewer may NOT add or edit nodes", viewer.post("/api/conns", json={"action": "add", "name": "x", "host": "1.2.3.4", "port": 4403}).status_code == 403
      and viewer.post("/api/conns", json={"action": "remove", "id": "main"}).status_code == 403)
check("a viewer POST without JSON is refused too", viewer.post("/api/conns", data="x", content_type="text/plain").status_code == 403)
cv = viewer.get("/api/conns").get_json()["conns"][0]
ca = admin.get("/api/conns").get_json()["conns"][0]
check("the viewer sees no host, port or agent address", set(cv) == {"id", "name", "online"} and "host" in ca and "agent" in ca, str(cv))
check("the status JSON hides them too and says the role", "host" not in json.dumps(viewer.get("/api/status").get_json()["conns"]) and viewer.get("/api/status").get_json()["role"] == "viewer")
check("admin keeps full access", admin.get("/api/audit").status_code == 200 and admin.get("/api/notify").status_code == 200)

# ---- Basic auth для скриптів ----
b = lambda u, p: {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()}
check("Basic auth as viewer: read ok, write forbidden", anon.get("/api/nodes", headers=b("viewer", VIEW_PW)).status_code == 200
      and anon.post("/api/send", json={"text": "x"}, headers=b("viewer", VIEW_PW)).status_code == 403)
check("Basic auth as admin still works", anon.get("/api/audit", headers=b("admin", ADMIN_PW)).status_code == 200)
check("Basic auth with a wrong password is 401", anon.get("/api/nodes", headers=b("viewer", "bad-bad-bad-1")).status_code == 401)

# ---- журнал дій ----
rows = admin.get("/api/audit").get_json()
acts = [(r["role"], r["user"], r["action"]) for r in rows]
check("logins are journalled with the role", ("admin", "admin", "вхід") in acts and ("viewer", "viewer", "вхід") in acts)
check("a failed login is journalled with the attempted name only", any(a[2] == "невдалий вхід" and a[1] == "viewer" for a in acts))
check("viewer refusals are journalled as the viewer", ("viewer", "viewer", "POST /api/send → 403") in acts)
check("admin changes are journalled", ("admin", "admin", "POST /api/viewer → 200") in acts)
check("blocked GETs of the viewer are not journalled (the page polls them)", not any("GET" in a[2] for a in acts))
check("node switching by the viewer is not journalled", not any("/api/conns → 200" in a[2] and a[0] == "viewer" for a in acts))
check("no password ever reaches the journal", VIEW_PW not in json.dumps(rows) and ADMIN_PW not in json.dumps(rows) and "nope-nope" not in json.dumps(rows))
anon.post("/api/send", json={"text": "x"})
check("an anonymous write attempt is journalled without a role", any(r["role"] == "" and r["action"] == "POST /api/send → 401" for r in admin.get("/api/audit").get_json()))
admin.post("/api/admin/job", json={"op": "read", "section": "lora", "target": "local"})
admin.post("/api/admin/job", json={"op": "write", "section": "lora", "target": "!000000aa", "value": {"tx_power": 99}})
acts2 = [r["action"] for r in admin.get("/api/audit").get_json()]
check("admin reads (POST /api/admin/job op=read) are not journalled", not any("admin/job read" in a for a in acts2))
check("an admin write is journalled with section and target but without values",
      any(a.startswith("POST /api/admin/job write lora → !000000aa") for a in acts2) and not any("99" in a for a in acts2), str([a for a in acts2 if "admin" in a]))
for k in range(2100):
    m.audit("тест")
n = m.db.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
check("the journal is capped", n <= 2000, str(n))

# ---- зміна пароля глядача й вимкнення ----
VIEW2 = "viewer-password-3"
check("changing the viewer password signs the viewer out", admin.post("/api/viewer", json={"password": VIEW2}).status_code == 200
      and viewer.get("/api/nodes").status_code == 401)
check("the old viewer password no longer works, the new one does", viewer.post("/login", data={"user": "viewer", "password": VIEW_PW}).status_code == 401
      and viewer.post("/login", data={"user": "viewer", "password": VIEW2}).status_code == 302)
check("the admin session survives that", admin.get("/api/audit").status_code == 200)
admin.post("/api/viewer", json={"clear": True})
check("clearing the role signs the viewer out and blocks logins", viewer.get("/api/nodes").status_code == 401
      and viewer.post("/login", data={"user": "viewer", "password": VIEW2}).status_code == 401 and not admin.get("/api/whoami").get_json()["viewer"])
sys.exit(0 if ok else 1)
