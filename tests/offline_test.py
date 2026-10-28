import base64, hashlib, os, subprocess, sys, tempfile
tmp = tempfile.mkdtemp()
os.environ["MESHPI_DB"] = os.path.join(tmp, "messages.db")
os.environ.pop("MESHPI_PASSWORD", None)
os.environ.pop("MESHPI_TILE_URL", None)
webui = os.path.abspath(sys.argv[1])
sys.path.insert(0, webui)
import app as m

ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print("ok  " if cond else "FAIL", name, extra)

page = m.app.test_client().get("/").get_data(as_text=True)
check("the page loads Leaflet from this server, not from unpkg", "/static/vendor/leaflet/leaflet.js" in page and "/static/vendor/leaflet/leaflet.css" in page
      and "unpkg.com" not in page)
sri = lambda f: "sha256-" + base64.b64encode(hashlib.sha256(open(os.path.join(webui, "static/vendor/leaflet", f), "rb").read()).digest()).decode()
check("the vendored Leaflet is byte-identical to the 1.9.4 release the page used to pin", sri("leaflet.js") == "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
      and sri("leaflet.css") == "sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=")
c = m.app.test_client()
check("the files and marker images are served", all(c.get("/static/vendor/leaflet/" + f).status_code == 200 for f in
      ("leaflet.js", "leaflet.css", "images/marker-icon.png", "images/layers.png", "images/marker-shadow.png", "LICENSE")))
check("the default tile source is OpenStreetMap", "https://tile.openstreetmap.org/{z}/{x}/{y}.png" in page and "maxZoom: 19" in page)


def tiles(url=None, zoom=None, attr=None):
    env = {**os.environ, "PYTHONPATH": webui}
    for k, v in (("MESHPI_TILE_URL", url), ("MESHPI_TILE_ZOOM", zoom), ("MESHPI_TILE_ATTR", attr)):
        env.pop(k, None)
        if v is not None:
            env[k] = v
    code = ("import app, json; c = app.app.test_client(); p = c.get('/').get_data(as_text=True); "
            "print(json.dumps([app.TILE_URL, app.TILE_ZOOM, app.TILE_ATTR, 'L.tileLayer' in p]))")
    return __import__("json").loads(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=webui, timeout=60).stdout.strip().splitlines()[-1])


own = tiles("http://tiles.test:8081/tiles/{z}/{x}/{y}.png", "16", "© свої плитки")
check("a self-hosted tile server can be configured (URL, max zoom, attribution)", own == ["http://tiles.test:8081/tiles/{z}/{x}/{y}.png", 16, "© свої плитки", True], str(own))
check("a URL without {z} falls back to OpenStreetMap", tiles("http://x/tiles.png")[0] == "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
check("a URL with quotes or script falls back (it ends up in the page)", tiles("http://x/{z}/'+alert(1)+'.png")[0].startswith("https://tile.openstreetmap.org")
      and tiles("javascript:alert(1)//{z}")[0].startswith("https://tile.openstreetmap.org"))
check("a nonsense zoom falls back to 19", tiles(zoom="abc")[1] == 19 and tiles(zoom="99")[1] == 22)
sys.exit(0 if ok else 1)
