import json, math, os, sys, tempfile, time
tmp = tempfile.mkdtemp()
os.environ["MESHPI_DB"] = os.path.join(tmp, "messages.db")
os.environ["MESHPI_NODES_FILE"] = os.path.join(tmp, "nodes.json")
os.environ["MESHPI_NOTIFY_FILE"] = os.path.join(tmp, "notify.json")
os.environ["MESHPI_SOLAR_FILE"] = os.path.join(tmp, "solar.json")
os.environ.pop("MESHPI_PASSWORD", None)
sys.path.insert(0, sys.argv[1])
import app as m
import extras as x

m.init_db(m.DB_PATH)
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print("ok  " if cond else "FAIL", name, extra)

# ---- профіль ----
P = x.sample_points(50.0, 30.0, 50.1, 30.2, 5)
check("sample points include both ends and are evenly spaced", P[0] == (50.0, 30.0) and P[-1] == (50.1, 30.2) and abs(P[2][0] - 50.05) < 1e-9)
flat = [100.0] * 21
r = x.link_profile(flat, 10.0, 10, 10, 433.5)
check("flat terrain, 10 m antennas, 10 km: line of sight is free but the first Fresnel zone is not (needs ~28 m masts)", r["verdict"] == "marginal" and r["min_clear"] > 0 and r["min_ratio"] < 0.6, str((r["verdict"], r["min_clear"], r["min_ratio"])))
check("flat terrain, 40 m antennas, 10 km: clear", x.link_profile(flat, 10.0, 40, 40, 433.5)["verdict"] == "clear")
check("the Earth bulge in the middle of 10 km is 25/(2*4/3*6371) km = 1.47 m", abs(r["points"][10]["bulge"] - 1.47) < 0.05, str(r["points"][10]["bulge"]))
check("Fresnel radius at the middle of 10 km at 433.5 MHz is ~ 17.32*sqrt(25/(0.4335*10)) = 41.6 m", abs(r["points"][10]["f1"] - 41.6) < 0.3, str(r["points"][10]["f1"]))
ridge = list(flat)
ridge[10] = 160.0
check("a ridge above the line of sight: blocked", x.link_profile(ridge, 10.0, 40, 40, 433.5)["verdict"] == "blocked")
hill = list(flat)
hill[10] = 100 + 40 - 20   # нижче променя на 20 м (мінус кривизна), але глибоко в першій зоні Френеля (її радіус ~42 м)
v = x.link_profile(hill, 10.0, 40, 40, 433.5)
check("below the line but inside the Fresnel zone: marginal", v["verdict"] == "marginal" and v["min_clear"] > 0 and v["min_ratio"] < 0.6, str(v["min_ratio"]))
high = x.link_profile(flat, 10.0, 80, 80, 433.5)
check("higher masts clear the zone", high["verdict"] == "clear")
check("at a higher frequency the Fresnel zone shrinks", x.link_profile(hill, 10.0, 40, 40, 868)["points"][10]["f1"] < v["points"][10]["f1"])
for bad in (([1, 2], 10, 3, 3, 433), ([1, 2, 3], 0, 3, 3, 433), ([1, 2, 3], 5, 3, 3, 0)):
    try:
        x.link_profile(*bad)
        check("bad profile input is refused " + str(bad[1:]), False)
    except ValueError:
        check("bad profile input is refused " + str(bad[1:]), True)
b = x.link_budget(1.0, 433.5, 10, 2, 2, 250, -17.5)
check("free space loss at 1 km, 433.5 MHz is 85.2 dB", abs(b["fspl"] - 85.2) < 0.1, str(b))
check("sensitivity for 250 kHz, NF 6, SNR -17.5 is -131.5 dBm", abs(b["sensitivity"] + 131.5) < 0.1)
check("margin = received - sensitivity", abs(b["margin"] - (b["prx"] - b["sensitivity"])) < 0.11 and abs(b["prx"] - (10 + 4 - 85.18)) < 0.1)
check("the margin shrinks by 6 dB when the distance doubles", abs(x.link_budget(2.0, 433.5, 10, 2, 2, 250, -17.5)["margin"] - b["margin"] + 6.02) < 0.1)

# ---- сонце ----
FJ = {"result": {"watts": {"2026-10-24 08:00:00": 0, "2026-10-24 09:00:00": 40, "2026-10-24 10:00:00": 80, "2026-10-24 11:00:00": 100, "2026-10-24 12:00:00": 100,
                           "2026-10-24 13:00:00": 80, "2026-10-24 14:00:00": 40, "2026-10-24 15:00:00": 0},
                 "watt_hours_day": {"2026-10-24": 440.0}},
      "message": {"info": {"time": "2026-10-24T08:00:00+03:00", "time_utc": "2026-10-24T05:00:00+00:00"}, "ratelimit": {"period": 3600, "limit": 12, "remaining": 9}}}
pts, days, left = x.parse_forecast(FJ)
import datetime as dt
check("... exactly: 08:00+03:00 is 05:00 UTC", dt.datetime.fromtimestamp(pts[0][0], dt.timezone.utc).strftime("%H:%M") == "05:00", str(pts[0]))
check("daily energy and the remaining requests", days == {"2026-10-24": 440.0} and left == 9 and len(pts) == 8)
check("junk entries are skipped", x.parse_forecast({"result": {"watts": {"bad": 1, "2026-10-24 09:00:00": "x", "2026-10-24 10:00:00": -5}}})[0] == [])
check("an empty or odd response does not crash", x.parse_forecast({}) == ([], {}, None))
CFG = {**x.solar_defaults(), "on": True, "lat": 50.0, "lon": 30.0, "kwp": 0.1, "cap_wh": 20.0, "load_w": 0.5, "eff": 0.8}
start = pts[0][0]
pr = x.solar_projection(pts, CFG, 50, start)
check("projection: generation 0.8 * 440 Wh over the day", abs(pr["gen_wh"] - 0.8 * 440) < 25 and pr["ok"], str(pr))
check("projection: load is 0.5 W over the horizon", abs(pr["load_wh"] - 0.5 * (pts[-1][0] - start) / 3600) < 0.3)
check("a sunny day charges the battery to full: no risk", not pr["risk"] and pr["end_pct"] > 90 and pr["min_pct"] <= 50)
dark = x.solar_projection(pts, {**CFG, "load_w": 20, "cap_wh": 20}, 30, start)
check("a heavy load empties it: risk with the time", dark["risk"] and dark["empty_at"] > start and dark["min_pct"] == 0.0)
check("unknown capacity: only the balance", "risk" not in x.solar_projection(pts, {**CFG, "cap_wh": 0}, None, start) and x.solar_projection(pts, {**CFG, "cap_wh": 0}, None, start)["ok"])
check("no data: not ok", x.solar_projection([], CFG, 50, start) == {"ok": False})
ok_, why = x.solar_update(x.solar_defaults(), {"on": True, "lat": 50.1, "lon": 30.2, "dec": 40, "az": -20, "kwp": 0.25, "cap_wh": 37, "load_w": 0.4})
check("a valid solar config", why is None and ok_["on"] and ok_["dec"] == 40 and ok_["kwp"] == 0.25 and isinstance(ok_["dec"], int))
for body in ({"on": True}, {"lat": 91}, {"dec": 120}, {"az": 200}, {"kwp": 0}, {"load_w": -1}, {"eff": 2}, {"lat": True}, {"lat": float("nan")}, {"cap_wh": "9"}, "x"):
    check(f"solar config refuses {body}", x.solar_update(x.solar_defaults(), body)[1])

# ---- застосунок з підміною зовнішніх запитів ----
class Fake:
    def __init__(self):
        mk = lambda n, name, lat, lon: {"num": n, "user": {"id": f"!{n:08x}", "shortName": name}, "position": {"latitude": lat, "longitude": lon}}
        self.nodesByNum = {0x100: {**mk(0x100, "Me", 50.0, 30.0), "deviceMetrics": {"batteryLevel": 64}}, 0x201: mk(0x201, "Far", 50.09, 30.0), 0x202: {"num": 0x202, "user": {"id": "!00000202"}}}
        self.nodes = {}

    def getMyNodeInfo(self):
        return self.nodesByNum[0x100]


m.use_iface(Fake())
c = m.app.test_client()
calls = []


def fake_fetch(url, **kw):
    calls.append(url)
    if "elevation" in url:
        n = url.split("latitude=")[1].split("&")[0].count(",") + 1
        return {"elevation": [100.0] * n}
    return FJ


m.fetch_json = fake_fetch
q0 = "/api/linkplan?a=!00000100&b=!00000201"
q = q0 + "&h1=40&h2=40"
j = c.get(q).get_json()
check("linkplan: profile, budget and the source note", j["profile"]["verdict"] == "clear" and abs(j["profile"]["dist_km"] - 10.0) < 0.1 and "margin" in j["budget"] and "Copernicus" in j["source"], str(j)[:120])
check("linkplan: the elevation request carries the coordinates and at most 100 points", len(calls) == 1 and calls[0].startswith(m.ELEVATION_URL) and calls[0].split("latitude=")[1].split("&")[0].count(",") <= 99)
c.get(q)
check("linkplan: the same request is served from the cache", len(calls) == 1)
check("linkplan: parameters change the result", c.get(q + "&freq=868").get_json()["params"]["freq"] == 868.0)
for bad in ("&h1=300", "&freq=5", "&tx=99", "&bw=0", "&snr=5", "&h1=abc"):
    check(f"linkplan refuses {bad}", c.get(q0 + bad).status_code == 400)
check("linkplan: the same node twice or a node without a position is a 400", c.get("/api/linkplan?a=!00000100&b=!00000100").status_code == 400
      and c.get("/api/linkplan?a=!00000100&b=!00000202").status_code == 400)
m.ext_cache.clear()
m.fetch_json = lambda url, **kw: {"elevation": [1, 2]}
check("linkplan: a malformed elevation answer is a 502", c.get(q + "&h1=2").status_code == 502)
def boom(url, **kw):
    raise OSError("нема мережі")
m.ext_cache.clear()
m.fetch_json = boom
r = c.get(q + "&h1=4")
check("linkplan: no network is a 502 with a reason, not a crash", r.status_code == 502 and "висоти" in r.get_json()["error"])
m.NO_EXTERNAL = True
check("linkplan: switched off by MESHPI_NO_EXTERNAL", c.get(q + "&h1=5").status_code == 403)
m.NO_EXTERNAL = False
m.fetch_json = fake_fetch

j = c.get("/api/solar").get_json()
check("solar: defaults, node position and battery, no forecast without the flag", j["cfg"]["on"] is False and j["node_pos"]["lat"] == 50.0 and j["battery"] == 64 and "forecast" not in j)
check("solar: a bad config is a 400", c.post("/api/solar", json={"on": True}).status_code == 400 and c.post("/api/solar", json={"kwp": -1}).status_code == 400)
r = c.post("/api/solar", json={"on": True, "lat": 50.0, "lon": 30.0, "dec": 35, "az": 0, "kwp": 0.1, "cap_wh": 20, "load_w": 0.5, "eff": 0.8})
check("solar: saved per connection", r.status_code == 200 and json.load(open(os.environ["MESHPI_SOLAR_FILE"]))["main"]["cap_wh"] == 20)
calls.clear()
j = c.get("/api/solar?forecast=1").get_json()
check("solar: forecast with the battery of the node as the start", j["forecast"]["ok"] and j["forecast"]["requests_left"] == 9 and "min_pct" in j["forecast"] and calls and calls[0].startswith(m.SOLAR_URL + "/50.0000/30.0000/35/0/0.1"), str(calls))
c.get("/api/solar?forecast=1")
check("solar: the forecast is cached for an hour (the free tier allows 12 requests an hour)", len(calls) == 1)
m.ext_cache.clear()
import urllib.error
def limited(url, **kw):
    raise urllib.error.HTTPError(url, 429, "Too Many", {}, None)
m.fetch_json = limited
check("solar: HTTP 429 is explained", "12 на годину" in c.get("/api/solar?forecast=1").get_json()["forecast"]["error"])
m.fetch_json = boom
check("solar: no network is explained", "не вдалося" in c.get("/api/solar?forecast=1").get_json()["forecast"]["error"])
m.NO_EXTERNAL = True
check("solar: with external calls off there is no forecast and the page says so", "forecast" not in c.get("/api/solar?forecast=1").get_json() and c.get("/api/solar").get_json()["external"] is False)
m.NO_EXTERNAL = False
page = c.get("/").get_data(as_text=True)
check("the page has both sections and the script", "planbox" in page and "solarbox" in page and c.get("/static/plan.js").status_code == 200)
check("fetch_json refuses redirects and huge answers (unit)", callable(m.fetch_json) and m._NoRedirectExt().redirect_request(None, None, 302, "", {}, "http://x") is None)
sys.exit(0 if ok else 1)
