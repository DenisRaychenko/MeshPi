import sys
from pathlib import Path

root = Path(sys.argv[1])
app = root / "app.py"
s = app.read_text(encoding="utf8")


def sub(old, new):
    global s
    assert s.count(old) == 1, f"expected exactly one match for: {old[:60]!r} (found {s.count(old)})"
    s = s.replace(old, new)


sub("from flask import Flask, Response, jsonify, render_template, request\n",
    "from datetime import timedelta\nfrom urllib.parse import quote\n\n"
    "import secrets\n"
    "from flask import Flask, Response, jsonify, redirect, render_template, request, session\n")

sub('app = Flask(__name__)\n',
    '''app = Flask(__name__)


def _load_secret():
    """Ключ підпису сесій: зберігається поруч із базою, щоб входи переживали перезапуск контейнера."""
    p = Path(DB_PATH).with_name("secret.key")
    try:
        text = p.read_text().strip()
        if len(text) >= 64:
            return bytes.fromhex(text)
    except (OSError, ValueError):
        pass
    key = secrets.token_bytes(32)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(p) + ".tmp"
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            f.write(key.hex())
        os.replace(tmp, p)
    except OSError:
        pass  # без запису ключ живе лише до перезапуску: тоді доведеться увійти знову
    return key


app.secret_key = _load_secret()
app.config.update(
    SESSION_COOKIE_NAME="meshpi_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("MESHPI_COOKIE_SECURE") == "1",  # 1 — лише коли перед панеллю є HTTPS
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)
''')

sub('''@app.before_request
def check_auth():
    if not auth_on():
        return None
    a = request.authorization
    if a and _eq(a.username, USER) and pw_ok(a.password or ""):
        return None
    return Response("Потрібна автентифікація", 401, {"WWW-Authenticate": 'Basic realm="meshpi"'})
''', '''def _auth_stamp():
    """Відбиток пароля в сесії: зміна пароля одразу анулює всі інші входи."""
    try:
        pw = Path(PWFILE).read_text()
    except OSError:
        pw = PASSWORD
    return hashlib.sha256(("meshpi:" + pw).encode()).hexdigest()[:16]


def _safe_next(n):
    return n if n.startswith("/") and not n.startswith("//") and "\\\\" not in n else "/"


@app.before_request
def check_auth():
    if not auth_on():
        return None
    if request.path in ("/login", "/logout") or request.path.startswith("/static/"):
        return None
    if session.get("ok") and hmac.compare_digest(str(session.get("stamp", "")), _auth_stamp()):
        return None
    a = request.authorization  # Basic залишено для скриптів і curl; вікно пароля браузер більше не показує
    if a and _eq(a.username, USER) and pw_ok(a.password or ""):
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="Потрібен вхід"), 401
    nxt = request.full_path.rstrip("?")
    return redirect("/login" + ("?next=" + quote(nxt) if nxt not in ("", "/") else ""))


@app.route("/login", methods=["GET", "POST"])
def login():
    nxt = _safe_next(request.values.get("next", "/"))
    if not auth_on():
        return redirect(nxt)
    if request.method == "POST":
        user = request.form.get("user", "")
        if len(_fails) == _fails.maxlen and time.time() - _fails[0] < 60:
            return render_template("login.html", error="Забагато невдалих спроб, зачекайте хвилину",
                                   next=nxt, user=user), 429
        if _eq(user, USER) and pw_ok(request.form.get("password", "")):
            session.clear()
            session["ok"] = True
            session["stamp"] = _auth_stamp()
            session.permanent = True
            return redirect(nxt)
        return render_template("login.html", error="Невірний користувач або пароль", next=nxt, user=user), 401
    return render_template("login.html", error=None, next=nxt, user="")


@app.post("/logout")
def logout():
    session.clear()
    return redirect("/login")
''')

sub('''@app.get("/")
def index():
    return render_template("index.html")


@app.get("/settings")
def settings_page():
    return render_template("settings.html")
''', '''@app.get("/")
def index():
    return render_template("app.html")


@app.get("/settings")
def settings_page():
    return redirect("/#node")  # налаштування тепер вкладки «Радіо», «Вузол», «Система»
''')

sub('''    write_private(PWFILE, f"{salt.hex()}${_scrypt(new, salt)}")
    _good = None
    return jsonify(ok=True)''', '''    write_private(PWFILE, f"{salt.hex()}${_scrypt(new, salt)}")
    _good = None
    session["stamp"] = _auth_stamp()  # цей браузер лишається ув'язаним, решту сесій буде скинуто
    return jsonify(ok=True)''')
app.write_text(s, encoding="utf8")

js = root / "static/common.js"
c = js.read_text(encoding="utf8")
old = "  const j = await r.json().catch(() => ({}));\n  if (!r.ok)"
assert c.count(old) == 1
c = c.replace(old, "  if (r.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname + location.hash); throw new Error('Потрібен вхід'); }\n" + old)
js.write_text(c, encoding="utf8")
print("patched app.py and common.js")
