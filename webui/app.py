"""Мінімальний веб-інтерфейс для meshtasticd (через проксі proxy.py до TCP API meshtasticd, порт 4404)."""
import base64
import binascii
import collections
import hashlib
import hmac
import io
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import urllib.error
import urllib.request
from datetime import timedelta
from urllib.parse import quote

import secrets

import admin
import segno
from flask import Flask, Response, has_request_context, jsonify, redirect, render_template, request, session
from google.protobuf.message import DecodeError  # type: ignore[import-untyped]
from meshtastic.protobuf import apponly_pb2, channel_pb2, config_pb2, mesh_pb2, portnums_pb2, telemetry_pb2
from meshtastic.tcp_interface import TCPInterface
from pubsub import pub
from waitress import serve

MESH_HOST = os.environ.get("MESH_HOST", "localhost")
# shortcut: API meshtasticd (4403) і проксі (4404) відкриті в LAN без пароля за задумом;
# для ненадійної мережі — файрвол
MESH_PORT = int(os.environ.get("MESH_PORT", "4404"))  # проксі (proxy.py); напряму до meshtasticd — 4403
USER = os.environ.get("MESHPI_USER", "admin")
PASSWORD = os.environ.get("MESHPI_PASSWORD", "")
DB_PATH = os.environ.get("MESHPI_DB", "messages.db")
# пароль, змінений в інтерфейсі (scrypt-хеш); /etc/meshpi.env доступний сервісу лише для читання
PWFILE = os.environ.get("MESHPI_PWFILE", str(Path(DB_PATH).with_name("password")))
# «моє розташування» для відстаней до вузлів
LOCFILE = os.environ.get("MESHPI_LOCFILE", str(Path(DB_PATH).with_name("location.json")))
PONGFILE = os.environ.get("MESHPI_PONGFILE", str(Path(DB_PATH).with_name("pong")))  # є файл — автовідповідь
# є файл — вхід без пароля (вимкнено в «Налаштуваннях»; рішення власника, як і відкриті 4403/4404)
NOAUTHFILE = os.environ.get("MESHPI_NOAUTHFILE", str(Path(DB_PATH).with_name("noauth")))
# пресет до сканування: повернути після збою
SCANFILE = os.environ.get("MESHPI_SCANFILE", str(Path(DB_PATH).with_name("scan")))
SCAN_SETTLE = 180  # с, скільки чекати повернення вузла після зміни пресета
PONG_GAP = 30  # с між відповідями одному вузлу: ефір спільний
PING_WORDS = {"ping", "пінг"}  # без огляду на регістр: ping, PING, Пінг, ПІНГ…
SAMPLE_EVERY = 300  # с між точками графіків: рідше — менше записів на флешку
KEEP = 1000  # скільки повідомлень тримати в базі
MAX_TEXT = 200  # байт; payload Meshtastic ~233
UNITS = ("meshtasticd", "meshpi-proxy", "meshpi-web")  # що можна читати в журналі
POWER_RE = re.compile(r"Set radio:.*power=(-?\d+)")
FINAL_RE = re.compile(r"Final Tx power: (-?\d+)")  # що прошивка справді виставила чипу
CFG_GLOB = "/etc/meshtasticd/config.d/*.yaml"
CAP_RE = re.compile(r"^\s*(?:SX126X|RF95)_MAX_POWER:\s*(\d+)", re.M)  # E22 / Ra-0x, RFM98PW
BUILD_RE = re.compile(r"^(restore|[1-9]|1[0-9]|2[0-2])$")  # 1..22 або restore
RADIO_ERR = re.compile(r"fail|error|busy|timeout|no such|not found", re.I)
# відомі нешкідливі рядки: бібліотека питає нові типи модулів, ненастроєні модулі, плата без RTC,
# busyRx — передачу відкладено, бо саме йде приймання (нормальна робота)
# error=-7 — пошкоджений (CRC) слабкий пакет на межі чутності: ефір, а не збій вузла
HARMLESS = re.compile(r"Unknown module config type|Could not open / read /prefs/|RTC not found|busyRx"
                      r"|error=-7 ")
DEST_RE = re.compile(r"^(\^all|![0-9a-f]{8})$")

LoRa = config_pb2.Config.LoRaConfig
app = Flask(__name__)


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
log = logging.getLogger("meshpi")
# ---- підключення до кількох вузлів ----
# Панель тримає з'єднання з усіма вузлами зі списку (nodes.json) одночасно, тож історія кожного пишеться постійно;
# у браузері показується вибраний (cookie-сесія). Кожен рядок в базі має власника — id підключення.
NODES_FILE = os.environ.get("MESHPI_NODES_FILE", str(Path(DB_PATH).with_name("nodes.json")))
CONN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")
# адреса агента на Pi: лише http(s)://хост[:порт] — без логіна в адресі, шляху й параметрів
AGENT_URL_RE = re.compile(r"^https?://[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(:[0-9]{1,5})?$")
AGENT_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{16,200}$")


def agent_url_ok(s):
    m = AGENT_URL_RE.match(s)
    port = s.rsplit(":", 1)[-1] if m and s.count(":") > 1 else None
    return bool(m) and (port is None or int(port) <= 65535)


class Conn:
    """Одне підключення до вузла: адреса (TCP API meshtasticd чи проксі), інтерфейс бібліотеки й стан."""

    def __init__(self, cid, name, host, port, agent="", agent_token=""):
        self.id, self.name, self.host, self.port = cid, name, host, int(port)
        self.agent, self.agent_token = agent, agent_token  # агент на Pi (pi-agent/): потужність з журналу meshtasticd
        self.agent_cache: tuple = (0.0, None)
        self.iface = None
        self.error = None
        self.dead = False  # підключення видалено — потік підключення завершується
        self.admin_lock = threading.Lock()  # адмін-запити по радіо йдуть по одному (спільний стан підтверджень)

    def public(self):  # токен агента браузеру не віддаємо ніколи
        return {"id": self.id, "name": self.name, "host": self.host, "port": self.port,
                "online": self.iface is not None, "error": self.error,
                "agent": self.agent, "agent_token_set": bool(self.agent_token)}


conns: dict[str, Conn] = {}
conns_lock = threading.Lock()
rssi: dict[str, float] = {}  # останній RSSI за вузлом (є лише в пакетах, не в списку вузлів)
db_lock = threading.Lock()
scan: dict = {"state": "idle"}  # idle | running | restoring | done | stopped | error
scan_stop = threading.Event()
scan_lock = threading.Lock()
db: sqlite3.Connection


def init_db(path):
    global db
    if "db" in globals():
        db.close()  # тести перевідкривають базу
    db = sqlite3.connect(path, check_same_thread=False)  # доступ під db_lock
    # WAL + NORMAL: менше записів і fsync на флешку; при втраті живлення можуть пропасти лише останні записи
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("CREATE TABLE IF NOT EXISTS msg (id INTEGER PRIMARY KEY, t INT, sender TEXT, name TEXT,"
               " dest TEXT, ch INT, text TEXT, snr REAL, out INT)")
    db.execute("CREATE TABLE IF NOT EXISTS probe (node TEXT, kind TEXT, t REAL, res TEXT)")  # див. save_probe
    # показники довкілля від вузлів (телеметрія): одна точка на вузол і величину не частіше ENV_MIN_GAP
    db.execute("CREATE TABLE IF NOT EXISTS env (t INT, node TEXT, hops INT, k TEXT, v REAL)")
    db.execute("CREATE INDEX IF NOT EXISTS env_t ON env (t)")
    # телеметрія пристрою й живлення кожного вузла (батарея, напруга, зайнятість каналу, INA…): для графіків у панелі вузла
    db.execute("CREATE TABLE IF NOT EXISTS tele (owner TEXT NOT NULL, node TEXT NOT NULL, t INT, k TEXT, v REAL)")
    db.execute("CREATE INDEX IF NOT EXISTS tele_node ON tele (owner, node, t)")
    # точки на карті (waypoints), прийняті з ефіру й надіслані нами; locked — номер вузла, якому належить редагування
    db.execute("CREATE TABLE IF NOT EXISTS waypoint (owner TEXT NOT NULL, id INT NOT NULL, lat REAL, lon REAL, name TEXT,"
               " descr TEXT, icon INT, expire INT, locked INT, sender TEXT, t INT, ch INT, PRIMARY KEY (owner, id))")
    # id пакета, на який відповідь (reply_id), і чи це реакція-емодзі — для реакцій як у застосунку
    cols = {r[1] for r in db.execute("PRAGMA table_info(msg)")}
    # ack: статус доставки приватного повідомлення — 0 надіслано, 1 хтось ретранслював, 2 дійшло до адресата, 3 помилка
    for c in ("pid INT", "reply INT", "emoji INT", "ack INT"):
        if c.split()[0] not in cols:  # база попередньої версії
            db.execute(f"ALTER TABLE msg ADD COLUMN {c}")
    # власник рядка — id підключення (див. Conn); база до кількох вузлів вважається даними підключення «main»
    for t in ("msg", "probe", "env"):
        if "owner" not in {r[1] for r in db.execute(f"PRAGMA table_info({t})")}:
            db.execute(f"ALTER TABLE {t} ADD COLUMN owner TEXT NOT NULL DEFAULT 'main'")
    # метрики графіків і добовий архів зв'язків (карта мережі): власник входить у первинний ключ
    for t, ddl, cols_old in (
            ("metric", "CREATE TABLE metric (owner TEXT NOT NULL DEFAULT 'main', t INT, chan REAL, air REAL, nodes INT,"
                       " PRIMARY KEY (owner, t))", "t, chan, air, nodes"),
            ("link", "CREATE TABLE link (owner TEXT NOT NULL DEFAULT 'main', day TEXT, src TEXT, dst TEXT, flow TEXT,"
                     " kind TEXT, n INT, snr_sum REAL, snr_n INT, t INT, PRIMARY KEY (owner, day, src, dst, flow, kind))",
             "day, src, dst, flow, kind, n, snr_sum, snr_n, t")):
        old = {r[1] for r in db.execute(f"PRAGMA table_info({t})")}
        if not old:
            db.execute(ddl)
        elif "owner" not in old:  # перенесення таблиці попередньої версії: одна транзакція (DDL у SQLite транзакційний)
            db.execute("BEGIN")
            try:
                db.execute(f"ALTER TABLE {t} RENAME TO {t}_old")
                db.execute(ddl)
                db.execute(f"INSERT INTO {t} (owner, {cols_old}) SELECT 'main', {cols_old} FROM {t}_old")
                db.execute(f"DROP TABLE {t}_old")
                db.commit()
            except sqlite3.Error:
                db.rollback()
                raise
    db.commit()  # зміни схеми й міграція мають дійти до файла, навіть якщо база одразу закриється (тести)


MSG_COLS = "t, sender, name, dest, ch, text, snr, out, pid, reply, emoji"


def add_msg(sender, name, dest, ch, text, snr, out, pid=None, reply=None, emoji=0, owner=None, ack=None):
    """True — записано; False — повтор уже збереженого пакета або збій бази."""
    owner = owner or cur_id()
    try:  # збій історії (повний диск, права) не має ламати радіо: повідомлення вже передане/прийняте
        with db_lock, db:
            # проксі після перепідключення повторює пропущені тексти — уже збережене не дублюємо
            q = "SELECT 1 FROM msg WHERE owner = ? AND pid = ? AND sender IS ?"
            if pid and db.execute(q, (owner, pid, sender)).fetchone():
                return False
            db.execute(f"INSERT INTO msg ({MSG_COLS}, owner, ack) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (int(time.time()), sender, name, dest, ch, text, snr, out, pid, reply, emoji, owner, ack))
            db.execute("DELETE FROM msg WHERE owner = ? AND id <= (SELECT MAX(id) FROM msg WHERE owner = ?) - ?",
                       (owner, owner, KEEP))
    except sqlite3.Error:
        log.exception("не вдалося записати повідомлення в історію")
        return False
    return True


def recent(n=200, owner=None):
    with db_lock:
        rows = db.execute(f"SELECT {MSG_COLS}, ack FROM msg WHERE owner = ? ORDER BY id DESC LIMIT ?",
                          (owner or cur_id(), n)).fetchall()
    return [{"t": t, "from": s, "name": nm, "to": d, "ch": c, "text": x, "snr": snr, "out": bool(o),
             "pid": pid, "reply": rep, "emoji": bool(e), "ack": ack}
            for t, s, nm, d, c, x, snr, o, pid, rep, e, ack in reversed(rows)]


def set_ack(owner, pid, status):
    """Статус доставки нашого приватного повідомлення (див. ack у init_db): лише вгору, доставлене не скасовується помилкою."""
    if not pid:
        return
    try:
        with db_lock, db:
            db.execute("UPDATE msg SET ack = ? WHERE owner = ? AND pid = ? AND out = 1 AND (ack IS NULL OR (ack < ? AND ack != 2))",
                       (status, owner, pid, status))
    except sqlite3.Error:
        log.exception("не вдалося записати статус доставки")


class OwnedTCP(TCPInterface):
    """TCP-інтерфейс, що знає свого власника (id підключення) вже з першого пакета: його читач стартує в конструкторі."""

    def __init__(self, owner, **kw):
        self.meshpi_owner = owner
        super().__init__(**kw)


run_threads = False  # у тестах потоки підключення не стартують


def drop_conn(c):
    c.dead = True
    i, c.iface = c.iface, None
    if i is not None:
        threading.Thread(target=lambda: _quiet(i.close), daemon=True).start()


def conn_loop(c):
    """Потік підключення: тримає з'єднання з одним вузлом, перепідключає кожні 5 с."""
    down = False  # пишемо в журнал лише зміну стану, не кожні 5 с
    while not c.dead:
        if c.iface is None:
            try:
                i = OwnedTCP(c.id, hostname=c.host, portNumber=c.port)
            except Exception as e:  # noqa: BLE001
                c.error = str(e)[:200]
                if not down:
                    log.warning("вузол «%s» (%s:%s) недоступний: %s (повторюю кожні 5 с, без нових повідомлень)",
                                c.name, c.host, c.port, e)
                down = True
            else:
                if c.dead:
                    _quiet(i.close)
                else:
                    c.iface, c.error, down = i, None, False
                    log.info("підключено до «%s» %s:%s", c.name, c.host, c.port)
        time.sleep(5)


def load_conns():
    """Привести реєстр підключень у відповідність до nodes.json; без файлу це одне підключення «main»
    з MESH_HOST/MESH_PORT. Існуючі з тією ж адресою лишаються (з'єднання не рветься)."""
    global conns
    try:
        items = json.loads(Path(NODES_FILE).read_text())
        if not isinstance(items, list):
            raise ValueError
    except (OSError, ValueError):
        items = [{"id": "main", "name": "Основний вузол", "host": MESH_HOST, "port": MESH_PORT,
                  "agent": os.environ.get("MESHPI_AGENT_URL", ""), "agent_token": os.environ.get("MESHPI_AGENT_TOKEN", "")}]
    with conns_lock:
        keep: dict[str, Conn] = {}
        for it in items:
            try:
                cid, host, port = str(it["id"]), str(it["host"]).strip(), int(it["port"])
                name = str(it.get("name") or cid)[:40]
                agent, token = str(it.get("agent") or ""), str(it.get("agent_token") or "")
                if not CONN_ID_RE.match(cid) or cid in keep or not host or not 1 <= port <= 65535:
                    continue
                if agent and not agent_url_ok(agent):
                    agent = token = ""
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            old = conns.get(cid)
            if old and (old.host, old.port) == (host, port):
                old.name = name
                if (old.agent, old.agent_token) != (agent, token):
                    old.agent, old.agent_token, old.agent_cache = agent, token, (0.0, None)
                keep[cid] = old
                continue
            if old:
                drop_conn(old)
            keep[cid] = Conn(cid, name, host, port, agent, token)
            if run_threads:
                threading.Thread(target=conn_loop, args=(keep[cid],), daemon=True).start()
        if not keep:
            keep["main"] = Conn("main", "Основний вузол", MESH_HOST, MESH_PORT)
            if run_threads:
                threading.Thread(target=conn_loop, args=(keep["main"],), daemon=True).start()
        for cid, old in conns.items():
            if cid not in keep:
                drop_conn(old)
        conns = keep


def cur_id():
    """Вибране в цій сесії підключення; поза запитом (фонові потоки, тести) — перше."""
    cid = session.get("node") if has_request_context() else None
    return cid if cid in conns else next(iter(conns))


def cur():
    return conns[cur_id()]


def cur_iface():
    return cur().iface


def owner_of(interface):
    return getattr(interface, "meshpi_owner", None) or next(iter(conns))


iface = None  # лише для тестів: use_iface() кладе сюди підставний інтерфейс


def use_iface(obj, cid=None):
    """Підставити готовий інтерфейс (тести): стає з'єднанням підключення cid або першого."""
    global iface
    iface = obj
    c = conns.get(cid) or next(iter(conns.values()))
    c.iface = obj
    try:
        obj.meshpi_owner = c.id
    except AttributeError:
        pass


def _eq(a, b):
    return hmac.compare_digest((a or "").encode(), (b or "").encode())


# shortcut: спільний ліміт, не за адресою: 20 невдалих входів за хвилину (scrypt дорогий на Pi Zero) блокують
# і власника на цю хвилину; перейти на ліміт за IP, якщо інтерфейс відкрито ззовні
_fails: collections.deque = collections.deque(maxlen=20)
_good: bytes | None = None  # sha256 прийнятого пароля: scrypt на кожен запит надто повільний


def _scrypt(pw, salt):
    return hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def pw_ok(pw):
    global _good
    d = hashlib.sha256(pw.encode()).digest()
    if _good is not None and hmac.compare_digest(d, _good):
        return True
    if len(_fails) == _fails.maxlen and time.time() - _fails[0] < 60:
        return False
    try:
        salt, h = Path(PWFILE).read_text().strip().split("$")
        ok = hmac.compare_digest(_scrypt(pw, bytes.fromhex(salt)), h)
    except FileNotFoundError:
        return _eq(pw, PASSWORD)
    except (OSError, ValueError):  # зіпсований файл: нікого не пускаємо
        return False
    if ok:
        _good = d
    else:
        _fails.append(time.time())
    return ok


def auth_on():
    """Чи питати пароль: його задано (env або змінений у інтерфейсі) і вхід без пароля не ввімкнено."""
    return bool(PASSWORD or Path(PWFILE).exists()) and not Path(NOAUTHFILE).exists()


def _auth_stamp():
    """Відбиток пароля в сесії: зміна пароля одразу анулює всі інші входи."""
    try:
        pw = Path(PWFILE).read_text()
    except OSError:
        pw = PASSWORD
    return hashlib.sha256(("meshpi:" + pw).encode()).hexdigest()[:16]


def _safe_next(n):
    return n if n.startswith("/") and not n.startswith("//") and "\\" not in n else "/"


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


@app.after_request
def no_framing(resp):
    """Чужий сайт не вбудує сторінки у фрейм (запам'ятований пароль + підступний клік по «Зберегти»)."""
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def all_nodes(i):
    """Усі відомі вузли. nodesByNum, а не nodes: вузол, від якого прийшли лише пакети (телеметрія, текст)
    без NodeInfo, бібліотека кладе тільки в nodesByNum — у списку його не було б."""
    # list: словник змінює потік бібліотеки
    return list((getattr(i, "nodesByNum", None) or {}).values()) if i else []


_JSON_TYPES = (str, int, float, bool, dict, list, tuple)


def plain(x):
    """Лише те, що серіалізується в JSON. Бібліотека кладе в словники вузлів і сирі protobuf-об'єкти (ключ «raw»,
    з'являється, щойно вузол отримав власний NodeInfo чи телеметрію), а jsonify на них падає з 500."""
    if isinstance(x, dict):
        return {k: plain(v) for k, v in x.items() if k != "raw" and (v is None or isinstance(v, _JSON_TYPES))}
    if isinstance(x, (list, tuple)):
        return [plain(v) for v in x if v is None or isinstance(v, _JSON_TYPES)]
    return x


def node_name(interface, node_id):
    user = ((interface.nodes or {}).get(node_id) or {}).get("user", {})
    return user.get("longName") or node_id


def sender_of(packet):
    """ID відправника. Для першого пакета від ще невідомого вузла бібліотека дає fromId=None —
    тоді з номера: інакше особисте впало б у «Канали», а відповідь на нього пішла б усім."""
    num = packet.get("from")
    return packet.get("fromId") or (f"!{num:08x}" if isinstance(num, int) else None)


def dest_of(packet):
    """Адресат: ^all або ID вузла. Бібліотека дає toId=None, якщо власного вузла немає в її списку
    (адресовані нам приватні тоді потрапляли б у «Канали», а наші відповіді — у вкладку розмови):
    тоді з номера."""
    num = packet.get("to")
    if packet.get("toId") or not isinstance(num, int):
        return packet.get("toId")
    return "^all" if num == 0xFFFFFFFF else f"!{num:08x}"


def on_text(packet, interface):
    sender = sender_of(packet)
    # через проксі приходить і текст, надісланий із застосунку від імені цього вузла
    mine = sender is not None and sender == (interface.getMyNodeInfo() or {}).get("user", {}).get("id")
    name = "Ви" if mine else node_name(interface, sender)
    dec = packet["decoded"]
    new = add_msg(sender, name, dest_of(packet), packet.get("channel", 0), dec["text"],
                  packet.get("rxSnr"), int(mine), packet.get("id"), dec.get("replyId"),
                  int(bool(dec.get("emoji"))), owner=owner_of(interface))
    if not mine and new and dec["text"].strip().casefold() in PING_WORDS:
        pong(packet, interface)


pong_last: dict[tuple, float] = {}  # (підключення, вузол) -> час останньої відповіді


def hops_word(n):
    """1 стрибок, 2–4 стрибки, 0 і 5+ стрибків (11–14 — стрибків)."""
    if n is None or n % 100 in (11, 12, 13, 14):
        return "стрибків"
    return "стрибок" if n % 10 == 1 else "стрибки" if n % 10 in (2, 3, 4) else "стрибків"


def pong(packet, i):
    """Автовідповідь на «ping»: «PONG» і кількість стрибків до відправника — туди ж, звідки прийшов
    (у канал або особисто), як відповідь на його повідомлення. Вмикається в «Налаштуваннях»."""
    sender, now, owner = sender_of(packet), time.time(), owner_of(i)
    if not sender or not Path(PONGFILE).exists() or now - pong_last.get((owner, sender), 0) < PONG_GAP:
        return
    if now - (packet.get("rxTime") or now) > 300:  # давній (проксі повторив пропущене) — не відповідаємо
        return
    if len(pong_last) > 200:
        pong_last.clear()
    pong_last[(owner, sender)] = now
    # стрибки = hopStart − hopLimit; нульових полів бібліотека не пише; без hopStart (стара прошивка) — «?»
    hs = packet.get("hopStart")
    n = hs - (packet.get("hopLimit") or 0) if hs else None
    text = f"Чую PING, відповідаю PONG, почув за {'?' if n is None else n} {hops_word(n)}"
    to, ch = ("^all" if dest_of(packet) in (None, "^all") else sender), packet.get("channel", 0)
    try:
        p = send_text(i, text, to, ch, packet.get("id"))
    except Exception:  # noqa: BLE001 — збій відправки не має ламати прийом
        log.exception("не вдалося відповісти PONG")
        return
    add_msg(None, "Ви", to, ch, text, None, 1, getattr(p, "id", None) or None, packet.get("id"), owner=owner)


# ---- довкілля: усереднення показників вузлів у межах кількох стрибків ----
ENV_MAX_HOPS = int(os.environ.get("MESHPI_ENV_HOPS", "3"))     # враховуємо вузли не далі за стільки стрибків
ENV_WINDOW = 3 * 3600      # с: «поточними» вважаємо показники не старші за це
ENV_KEEP = 7 * 86400
ENV_MIN_GAP = 300          # с між записами однієї величини одного вузла
ENV_BUCKET = 1800          # с: крок історії для міні-графіка
# ключ у телеметрії -> (id, підпис, одиниця, допустимий діапазон; усе поза ним — збій датчика, відкидаємо)
ENV_METRICS = {
    "temperature": ("temperature", "Температура", "°C", (-60, 80)),
    "relativeHumidity": ("humidity", "Вологість", "%", (0, 100)),
    "barometricPressure": ("pressure", "Тиск", "гПа", (300, 1100)),
    "iaq": ("iaq", "Якість повітря (IAQ)", "", (0, 500)),
    "gasResistance": ("gas", "Опір газу", "МОм", (0, 100000)),
    "lux": ("lux", "Освітленість", "лк", (0, 200000)),
    "windSpeed": ("wind", "Вітер", "м/с", (0, 120)),
}
AIR_METRICS = {
    "pm25Standard": ("pm25", "PM2.5", "мкг/м³", (0, 1000)),
    "co2": ("co2", "CO₂", "ppm", (0, 10000)),
}
ENV_ORDER = ["temperature", "humidity", "pressure", "iaq", "gas", "lux", "wind", "pm25", "co2"]
ENV_INFO = {m[0]: m for m in [*ENV_METRICS.values(), *AIR_METRICS.values()]}
ENV_DIGITS = {"temperature": 1, "humidity": 0, "pressure": 1, "iaq": 0, "gas": 1, "lux": 0, "wind": 1,
              "pm25": 0, "co2": 0}


def record_env(packet, owner=None):
    """Зберегти показники довкілля з пакета телеметрії (якщо це він і значення правдоподібні)."""
    owner = owner or cur_id()
    if packet.get("viaMqtt"):
        return  # пакет прийшов через MQTT-міст, а не з радіоефіру
    dec = packet.get("decoded") or {}
    if dec.get("portnum") != "TELEMETRY_APP":
        return
    tel = dec.get("telemetry") or {}
    sender = sender_of(packet)
    if not sender:
        return
    hs = packet.get("hopStart")
    hops = hs - (packet.get("hopLimit") or 0) if hs else None
    vals = []
    for block, table in ((tel.get("environmentMetrics"), ENV_METRICS), (tel.get("airQualityMetrics"), AIR_METRICS)):
        for key, (mid, _label, _unit, (lo, hi)) in table.items():
            v = (block or {}).get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi:
                vals.append((mid, float(v)))
    if not vals:
        return
    now = int(time.time())
    try:
        with db_lock, db:
            for mid, v in vals:
                last = db.execute("SELECT MAX(t) FROM env WHERE owner = ? AND node = ? AND k = ?",
                                  (owner, sender, mid)).fetchone()[0]
                if last and now - last < ENV_MIN_GAP:
                    continue
                db.execute("INSERT INTO env (t, node, hops, k, v, owner) VALUES (?,?,?,?,?,?)",
                           (now, sender, hops, mid, v, owner))
            db.execute("DELETE FROM env WHERE t < ?", (now - ENV_KEEP,))
    except sqlite3.Error:
        log.exception("не вдалося записати показники довкілля")


def env_summary():
    """Середнє за вузлами в межах ENV_MAX_HOPS стрибків: беремо останній показник кожного вузла (не старший
    за ENV_WINDOW), усереднюємо між вузлами; для міні-графіка — те саме по півгодинних відрізках за добу."""
    now = int(time.time())
    i, owner = cur_iface(), cur_id()
    hops_now = {(n.get("user") or {}).get("id"): n.get("hopsAway") for n in all_nodes(i)}
    mqtt_ids = {(n.get("user") or {}).get("id") for n in all_nodes(i) if n.get("viaMqtt")}  # вузли лише через MQTT

    def hops_of(node, h):
        if node in mqtt_ids:
            return None
        h = h if h is not None else hops_now.get(node)
        return h if h is not None and h <= ENV_MAX_HOPS else None

    with db_lock:
        recent = db.execute("SELECT node, k, v, t, hops FROM env WHERE owner = ? AND t >= ? ORDER BY t",
                            (owner, now - ENV_WINDOW)).fetchall()
        day = db.execute("SELECT node, k, v, t, hops FROM env WHERE owner = ? AND t >= ? ORDER BY t",
                         (owner, now - 86400)).fetchall()
    latest, sources = {}, {}
    for node, k, v, t, h in recent:
        latest[(node, k)] = (v, t, h)
    values: dict[str, list[float]] = {}
    for (node, k), (v, t, h) in latest.items():
        hh = hops_of(node, h)
        if hh is None:
            continue
        values.setdefault(k, []).append(v)
        src = sources.setdefault(node, {"id": node, "name": node_name(i, node), "hops": hh, "t": t, "values": {}})
        src["values"][k] = round(v, ENV_DIGITS.get(k, 1))
        src["t"] = max(src["t"], t)
    # історія: усереднення спочатку в межах вузла (щоб балакучий вузол не переважив інших), потім між вузлами
    buckets: dict[tuple[str, int], dict[str, list[float]]] = {}
    for node, k, v, t, h in day:
        if hops_of(node, h) is None:
            continue
        buckets.setdefault((k, t // ENV_BUCKET * ENV_BUCKET), {}).setdefault(node, []).append(v)
    series: dict[str, list[list[float]]] = {}
    for (k, b), per_node in sorted(buckets.items(), key=lambda kv: kv[0][1]):
        means = [sum(x) / len(x) for x in per_node.values()]
        series.setdefault(k, []).append([b + ENV_BUCKET // 2, round(sum(means) / len(means), ENV_DIGITS.get(k, 1))])
    metrics = []
    for k in ENV_ORDER:
        vs = values.get(k)
        if not vs:
            continue
        d = ENV_DIGITS.get(k, 1)
        mid, label, unit, _ = ENV_INFO[k]
        metrics.append({"id": mid, "label": label, "unit": unit, "avg": round(sum(vs) / len(vs), d),
                        "min": round(min(vs), d), "max": round(max(vs), d), "n": len(vs),
                        "series": series.get(k, [])})
    srcs = sorted(sources.values(), key=lambda x: (x["hops"], -x["t"]))
    for x in srcs:
        x["age"] = now - x.pop("t")
    return {"max_hops": ENV_MAX_HOPS, "window": ENV_WINDOW, "metrics": metrics, "sources": srcs, "now": now}


@app.get("/api/env")
def api_env():
    return jsonify(env_summary())


def env_latest(nodes_list):
    """Останні показники довкілля кожного вузла за добу: {id: ({величина: значення}, час)}.
    З бази; якщо там порожньо, беремо те, що бібліотека запам'ятала з пакетів цього сеансу."""
    now = int(time.time())
    out: dict[str, tuple[dict, int]] = {}
    with db_lock:
        rows = db.execute("SELECT node, k, v, t FROM env WHERE owner = ? AND t >= ? ORDER BY t",
                          (cur_id(), now - 86400)).fetchall()
    for node, k, v, t in rows:
        vals, last = out.get(node, ({}, 0))
        vals[k] = round(v, ENV_DIGITS.get(k, 1))
        out[node] = (vals, max(last, t))
    for n in nodes_list:
        nid = (n.get("user") or {}).get("id")
        if not nid or nid in out:
            continue
        vals = {}
        for block, table in ((n.get("environmentMetrics"), ENV_METRICS), (n.get("airQualityMetrics"), AIR_METRICS)):
            for key, (mid, _l, _u, (lo, hi)) in (table.items() if block else ()):
                v = block.get(key)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi:
                    vals[mid] = round(float(v), ENV_DIGITS.get(mid, 1))
        if vals:
            out[nid] = (vals, int(n.get("lastHeard") or now))
    return out


# ---- телеметрія вузлів: історія для графіків ----
TELE_KEEP = 7 * 86400
TELE_MIN_GAP = 120  # с між точками однієї величини одного вузла
# ключ у телеметрії -> (id серії, підпис, одиниця, допустимий діапазон)
TELE_DEVICE = {
    "batteryLevel": ("bat", "Батарея", "%", (0, 101)), "voltage": ("volt", "Напруга", "В", (0, 40)),
    "channelUtilization": ("chan", "Зайнятість каналу", "%", (0, 100)), "airUtilTx": ("air", "Ефір TX", "%", (0, 100)),
    "uptimeSeconds": ("up", "Час роботи", "год", (0, 10**9)),
}
TELE_POWER = {f"ch{n}{k}": (f"p{n}{s}", f"Канал {n}: {name}", unit, (-1e5, 1e5))  # INA219/226/260: до 3 каналів
              for n in (1, 2, 3) for k, s, name, unit in (("Voltage", "v", "напруга", "В"), ("Current", "c", "струм", "мА"))}
TELE_INFO = {v[0]: v for v in [*TELE_DEVICE.values(), *TELE_POWER.values()]}


def record_tele(packet, owner=None):
    """Зберегти показники пристрою й живлення з пакета телеметрії (точки не частіше TELE_MIN_GAP на величину й вузол)."""
    owner = owner or cur_id()
    dec = packet.get("decoded") or {}
    if dec.get("portnum") != "TELEMETRY_APP" or packet.get("viaMqtt"):
        return
    tel, sender = dec.get("telemetry") or {}, sender_of(packet)
    if not sender:
        return
    vals = []
    for block, table in ((tel.get("deviceMetrics"), TELE_DEVICE), (tel.get("powerMetrics"), TELE_POWER)):
        for key, (sid, _label, _unit, (lo, hi)) in table.items():
            v = (block or {}).get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi:
                vals.append((sid, float(v) / 3600 if sid == "up" else float(v)))
    if not vals:
        return
    now = int(time.time())
    try:
        with db_lock, db:
            for sid, v in vals:
                last = db.execute("SELECT MAX(t) FROM tele WHERE owner = ? AND node = ? AND k = ?", (owner, sender, sid)).fetchone()[0]
                if last and now - last < TELE_MIN_GAP:
                    continue
                db.execute("INSERT INTO tele (owner, node, t, k, v) VALUES (?,?,?,?,?)", (owner, sender, now, sid, v))
            db.execute("DELETE FROM tele WHERE t < ?", (now - TELE_KEEP,))
    except sqlite3.Error:
        log.exception("не вдалося записати телеметрію вузла")


def thin(points, limit=240):
    """Не більше limit точок: середнє по рівних відрізках часу (графік у браузері й так не покаже більше)."""
    if len(points) <= limit:
        return points
    t0, t1 = points[0][0], points[-1][0]
    step = max((t1 - t0) / limit, 1)
    buckets: dict[int, list] = {}
    for t, v in points:
        buckets.setdefault(int((t - t0) / step), []).append((t, v))
    return [[sum(t for t, _ in b) // len(b), sum(v for _, v in b) / len(b)] for _, b in sorted(buckets.items())]


@app.get("/api/telemetry")
def api_telemetry():
    """Історія телеметрії вузла (?node=!id&hours=24): пристрій, живлення (INA) і довкілля; для вузла панелі ще й
    зайнятість каналу, яку панель міряє сама."""
    node = request.args.get("node", "")
    try:
        hours = min(max(int(request.args.get("hours", 24)), 1), 168)
    except ValueError:
        return err("hours: число")
    if not re.fullmatch(r"![0-9a-f]{8}", node):
        return err("node: !xxxxxxxx")
    owner, since = cur_id(), int(time.time()) - hours * 3600
    i = cur_iface()
    me = ((i.getMyNodeInfo() or {}).get("user") or {}).get("id") if i else None
    with db_lock:
        tele = db.execute("SELECT k, t, v FROM tele WHERE owner = ? AND node = ? AND t >= ? ORDER BY t", (owner, node, since)).fetchall()
        env = db.execute("SELECT k, t, v FROM env WHERE owner = ? AND node = ? AND t >= ? ORDER BY t", (owner, node, since)).fetchall()
        own = db.execute("SELECT t, chan, air FROM metric WHERE owner = ? AND t >= ? ORDER BY t", (owner, since)).fetchall() \
            if node == me else []
    series: dict[str, list] = {}
    for k, t, v in [*tele, *env]:
        series.setdefault(k, []).append([t, v])
    if own and "chan" not in series:  # свій вузол: його власні виміри кожні 5 хв щільніші за телеметрію в ефірі
        series["chan"] = [[t, c] for t, c, _a in own if c is not None]
        series["air"] = [[t, a] for t, _c, a in own if a is not None]
    info = {**TELE_INFO, **ENV_INFO}
    out = []
    for k in [*(v[0] for v in TELE_DEVICE.values()), *ENV_ORDER, *(v[0] for v in TELE_POWER.values())]:
        pts = series.get(k)
        if pts and k in info:
            out.append({"id": k, "label": info[k][1], "unit": info[k][2], "points": thin(pts)})
    return jsonify(node=node, hours=hours, series=out)


# ---- карта мережі: хто кого чує ----
# З кожного прийнятого пакета: відправник → ретранслятор → цей вузол (relayNode — лише останній байт id,
# тож ретранслятора шукаємо серед відомих вузлів). З «Маршруту» — повний шлях туди (down) і назад (up);
# з NeighborInfo — сусіди вузла. Напрямки відносно цього вузла (центр карти):
#   up   — «вгору», до нас: те, що вузли передають нам (приймання, шлях назад);
#   down — «вниз», від нас: те, що передаємо ми (шлях «туди» з перевірки маршруту).
# kind: direct — пряме радіочуття; relay — ретранслятор, що передав чужий пакет нам; via — шлях через
# невідомі проміжні вузли (пунктир).
LINK_KEEP = 60 * 86400   # с: скільки тримати добовий архів
LINK_FLUSH = 30          # с: зв'язки збираються в пам'яті й пишуться в базу пачкою (менше записів на диск)
link_buf: dict[tuple, list] = {}  # (підключення, день, src, dst, flow, kind) -> [пакетів, сума SNR, кількість SNR, час]
link_lock = threading.Lock()
link_flushed = 0.0
ROUTER_ROLES = {"ROUTER", "ROUTER_CLIENT", "ROUTER_LATE", "REPEATER"}


def day_of(t=None):
    return time.strftime("%Y-%m-%d", time.localtime(t))


def node_id(num):
    return f"!{num:08x}"


def add_link(src, dst, flow, kind, snr=None, t=None, owner=None):
    if not src or not dst or src == dst:
        return
    t = int(t or time.time())
    with link_lock:
        e = link_buf.setdefault((owner or cur_id(), day_of(t), src, dst, flow, kind), [0, 0.0, 0, t])
        e[0] += 1
        e[3] = t
        if isinstance(snr, (int, float)) and not isinstance(snr, bool) and math.isfinite(snr):
            e[1] += snr
            e[2] += 1


def flush_links(force=False):
    global link_flushed
    now = time.time()
    if not force and now - link_flushed < LINK_FLUSH:
        return
    link_flushed = now
    with link_lock:
        items = list(link_buf.items())
        link_buf.clear()
    if not items:
        return
    try:
        with db_lock, db:
            for (owner, day, a, b, flow, kind), (n, ssum, scnt, t) in items:
                db.execute("INSERT INTO link (owner, day, src, dst, flow, kind, n, snr_sum, snr_n, t)"
                           " VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT (owner, day, src, dst, flow, kind)"
                           " DO UPDATE SET n = n + excluded.n, snr_sum = snr_sum + excluded.snr_sum,"
                           " snr_n = snr_n + excluded.snr_n, t = MAX(t, excluded.t)",
                           (owner, day, a, b, flow, kind, n, ssum, scnt, t))
            db.execute("DELETE FROM link WHERE t < ?", (int(now) - LINK_KEEP,))
    except sqlite3.Error:
        log.exception("не вдалося записати зв'язки між вузлами")


def resolve_relay(i, byte, exclude):
    """Вузол за останнім байтом id. Ретранслятор — наш прямий сусід: з кількох кандидатів беремо того, чий
    hopsAway найменший, і лише якщо він один такий; невизначеність — None (краще без лінії, ніж хибна)."""
    cands = [n for n in all_nodes(i) if isinstance(n.get("num"), int) and n["num"] & 0xFF == byte
             and node_id(n["num"]) not in exclude and not n.get("viaMqtt")]
    if not cands:
        return None
    best = min((n.get("hopsAway") if n.get("hopsAway") is not None else 99) for n in cands)
    top = [n for n in cands if (n.get("hopsAway") if n.get("hopsAway") is not None else 99) == best]
    return node_id(top[0]["num"]) if len(top) == 1 else None


def record_link(packet, i):
    """Зв'язки з прийнятого з ефіру пакета (див. вище)."""
    if packet.get("viaMqtt") or i is None:
        return
    owner = owner_of(i)

    def put(src, dst, flow, kind, snr=None, t=None):
        add_link(src, dst, flow, kind, snr, t, owner=owner)

    me = ((i.getMyNodeInfo() or {}).get("user") or {}).get("id")
    sender = sender_of(packet)
    if not me or not sender:
        return
    t, snr = packet.get("rxTime") or time.time(), packet.get("rxSnr")
    if sender == me:  # наш пакет, який сусід ретранслював і ми почули: так видно шлях «вниз» без «Маршруту»
        hs = packet.get("hopStart")
        used = hs - (packet.get("hopLimit") or 0) if hs else None
        byte = packet.get("relayNode")
        relay = resolve_relay(i, byte, {me}) if isinstance(byte, int) else None
        if relay and used:
            put(me, relay, "down", "direct" if used == 1 else "via", snr, t)
        return
    dec = packet.get("decoded") or {}
    if dec.get("portnum") == "NEIGHBORINFO_APP":  # вузол повідомляє, кого він сам чує
        info = dec.get("neighborinfo") or dec.get("neighborInfo") or {}
        for nb in info.get("neighbors") or []:
            if isinstance(nb.get("nodeId"), int):
                put(node_id(nb["nodeId"]), sender, "up", "direct", nb.get("snr"), t)
    hs = packet.get("hopStart")
    used = hs - (packet.get("hopLimit") or 0) if hs else None
    byte = packet.get("relayNode")
    if used == 0:
        put(sender, me, "up", "direct", snr, t)
        return
    if not isinstance(byte, int):
        return
    relay = resolve_relay(i, byte, {me, sender})
    if relay is None:
        return
    put(relay, me, "up", "relay", snr, t)
    if used == 1:
        put(sender, relay, "up", "direct", None, t)
    elif used and used > 1:
        put(sender, relay, "up", "via", None, t)


def record_trace(data, owner=None):
    """Повний шлях з відповіді «Маршруту»: туди — «вниз», назад — «вгору»; кожен крок — пряме чуття."""
    for key, flow in (("towards", "down"), ("back", "up")):
        path = data.get(key) or []
        for a, b in zip(path, path[1:]):
            if a["id"] != "!ffffffff" and b["id"] != "!ffffffff":  # 0xffffffff — невідомий вузол на шляху
                add_link(a["id"], b["id"], flow, "direct", b.get("snr"), owner=owner)


PKT_LOG = 300  # скільки останніх пакетів тримати в пам'яті для «Журналу пакетів» (на кожне підключення)
pkt_log: dict[str, collections.deque] = {}


def log_packet(packet, owner):
    dec = packet.get("decoded")
    hs = packet.get("hopStart")
    relay = packet.get("relayNode")
    pkt_log.setdefault(owner, collections.deque(maxlen=PKT_LOG)).append({
        "t": int(packet.get("rxTime") or time.time()), "from": sender_of(packet), "to": dest_of(packet),
        "port": (dec or {}).get("portnum") or "ЗАШИФРОВАНО", "snr": packet.get("rxSnr"), "rssi": packet.get("rxRssi"),
        "hops": hs - (packet.get("hopLimit") or 0) if hs else None, "relay": f"{relay:02x}" if isinstance(relay, int) else None,
        "mqtt": bool(packet.get("viaMqtt")), "ch": packet.get("channel"), "id": packet.get("id")})


@app.get("/api/packets")
def api_packets():
    """Останні прийняті пакети вибраного вузла (найновіші першими) — як «Журнал пакетів» у застосунку."""
    try:
        n = min(max(int(request.args.get("limit", 100)), 1), PKT_LOG)
    except ValueError:
        return err("limit: число")
    i = cur_iface()
    rows = list(pkt_log.get(cur_id(), ()))[::-1][:n]
    for r in rows:
        r["from_name"] = node_name(i, r["from"]) if i and r["from"] else None
    return jsonify(rows)


def on_packet(packet, interface):
    owner = owner_of(interface)
    _quiet(lambda: log_packet(packet, owner))
    _quiet(lambda: record_env(packet, owner))
    _quiet(lambda: record_tele(packet, owner))
    _quiet(lambda: record_waypoint(packet, owner))
    _quiet(lambda: record_link(packet, interface))
    _quiet(flush_links)
    if packet.get("rxRssi") is not None and sender_of(packet):
        rssi[(owner, sender_of(packet))] = packet["rxRssi"]
    if scan.get("listening") and scan.get("conn") == owner:
        scan_hear(packet, interface)


def on_lost(interface):
    for c in list(conns.values()):
        if c.iface is interface:
            c.iface = None  # потік підключення за кілька секунд відновить з'єднання
    # закриваємо поза потоком читання інтерфейсу, щоб не блокувати його
    threading.Thread(target=lambda: _quiet(interface.close), daemon=True).start()


def _quiet(fn):
    try:
        fn()
    except Exception:  # noqa: BLE001
        pass


def sample():
    """Одна точка для графіків кожного підключеного вузла; тримаємо 7 діб."""
    for c in list(conns.values()):
        i = c.iface
        if i is None:
            continue
        m = (i.getMyNodeInfo() or {}).get("deviceMetrics", {})
        now = int(time.time())
        try:
            with db_lock, db:
                db.execute("INSERT OR REPLACE INTO metric (owner, t, chan, air, nodes) VALUES (?,?,?,?,?)",
                           (c.id, now, m.get("channelUtilization"), m.get("airUtilTx"),
                            sum(1 for n in all_nodes(i) if not n.get("viaMqtt"))))
                db.execute("DELETE FROM metric WHERE t < ?", (now - 7 * 86400,))
        except sqlite3.Error:
            log.exception("не вдалося записати метрики")


def sampler():
    while True:
        time.sleep(SAMPLE_EVERY)
        _quiet(sample)
        _quiet(flush_links)


def err(msg, code=400):
    return jsonify(error=msg), code


class Offline(Exception):
    """meshtasticd недоступний: обробник нижче відповідає 503."""


@app.errorhandler(Offline)
def _offline(_e):
    return err("meshtasticd недоступний", 503)


def need():
    """З'єднання з вибраним вузлом або 503 для маршруту."""
    i = cur_iface()
    if i is None:
        raise Offline
    return i


def names(long, short):
    """(назва, коротка) без пробілів по краях, якщо вміщуються в поля прошивки (39 і 4 байти); інакше None."""
    if not isinstance(long, str) or not isinstance(short, str):
        return None
    long, short = long.strip(), short.strip()
    try:
        fits = len(long.encode()) <= 39 and len(short.encode()) <= 4
    except UnicodeEncodeError:  # сурогат із JSON
        return None
    return (long, short) if long and short and fits else None


def write_private(path, text):
    """Атомарний запис із правами 600: обрив живлення не залишить порожнього чи обрізаного файла."""
    tmp = path + ".tmp"
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(text)
    os.replace(tmp, path)


def unchanged_304(resp):
    """ETag: сторінка опитує кожні 3–5 с, а дані змінюються рідко — замість повного JSON іде 304 без тіла."""
    resp.add_etag()
    return resp.make_conditional(request)


def body():
    d = request.get_json()  # 415, якщо не JSON — заодно захист від форм з чужих сайтів
    return d if isinstance(d, dict) else {}


@app.get("/")
def index():
    return render_template("app.html")


@app.get("/settings")
def settings_page():
    return redirect("/#node")  # налаштування тепер вкладки «Радіо», «Вузол», «Система»


def _name(enum, v):
    # прошивка новіша за бібліотеку може мати значення, якого бібліотека не знає
    return enum.Name(v) if v in enum.values() else str(v)


def home():
    """Розташування цієї плати для відстаней ({lat, lon}) або None. Лише на сторінці — в ефір не йде."""
    try:
        d = json.loads(Path(LOCFILE).read_text())
        return {"lat": float(d["lat"]), "lon": float(d["lon"])}
    except (OSError, ValueError, KeyError, TypeError):
        return None


@app.get("/api/location")
def get_location():
    return jsonify(home=home())


@app.post("/api/location")
def set_location():
    d = body()
    if d.get("lat") is None and d.get("lon") is None:  # очистити
        Path(LOCFILE).unlink(missing_ok=True)
        return jsonify(home=None)
    lat, lon = d.get("lat"), d.get("lon")
    num = all(type(v) in (int, float) and math.isfinite(v) for v in (lat, lon))  # bool — не число
    if not num or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return err("Широта −90…90, довгота −180…180 (числа)")
    write_private(LOCFILE, json.dumps({"lat": round(lat, 6), "lon": round(lon, 6)}))
    return jsonify(home=home())


@app.post("/api/pong")
def set_pong():
    on = body().get("on")
    if not isinstance(on, bool):
        return err("on: true або false")
    if on:
        write_private(PONGFILE, "")
    else:
        Path(PONGFILE).unlink(missing_ok=True)
    return jsonify(pong=on)


def mqtt_info(i):
    """MQTT на вузлі: чи ввімкнено модуль і які канали вивантажують/завантажують (прапорці каналів діють лише
    при ввімкненому модулі). Логін і пароль до MQTT-сервера не віддаємо."""
    try:
        mq = i.localNode.moduleConfig.mqtt
        chans = [{"index": c.index, "name": c.settings.name or f"канал {c.index}",
                  "uplink": bool(c.settings.uplink_enabled), "downlink": bool(c.settings.downlink_enabled)}
                 for c in (i.localNode.channels or []) if c.role != channel_pb2.Channel.DISABLED]
        return {"enabled": bool(mq.enabled), "address": mq.address, "root": mq.root,
                "proxy": bool(mq.proxy_to_client_enabled), "map": bool(mq.map_reporting_enabled),
                "tls": bool(mq.tls_enabled), "json": bool(mq.json_enabled), "channels": chans}
    except Exception:  # noqa: BLE001 — модулі ще не завантажені: просто без рядка MQTT
        return None


@app.get("/api/status")
def status():
    i = cur_iface()
    if i is None:
        return jsonify(connected=False, auth=auth_on(), conn=cur_id(), conns=[c.public() for c in conns.values()])
    me = i.getMyNodeInfo() or {}
    lora = i.localNode.localConfig.lora
    return jsonify(
        conn=cur_id(), conns=[c.public() for c in conns.values()],
        mqtt=mqtt_info(i),
        connected=True, node=plain(me.get("user", {})), metrics=plain(me.get("deviceMetrics", {})),
        hop_limit=lora.hop_limit, channel_num=lora.channel_num, node_count=sum(1 for n in all_nodes(i) if not n.get("viaMqtt")),
        region=_name(LoRa.RegionCode, lora.region), preset=_name(LoRa.ModemPreset, lora.modem_preset),
        tx_power=lora.tx_power, override_frequency=round(lora.override_frequency, 3), max_text=MAX_TEXT,
        home=home(), pong=Path(PONGFILE).exists(), auth=auth_on(),
        regions=list(LoRa.RegionCode.keys()), presets=list(LoRa.ModemPreset.keys()),
    )


def sh(*cmd):
    env = {**os.environ, "LC_ALL": "C.UTF-8"}  # вивід apt англійською (його розбираємо), UTF-8 для журналу
    try:
        # errors=replace: обрізаний посеред літери UTF-8 рядок журналу не має ламати сторінку
        return subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=10,
                              env=env).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def systemctl(*args):
    """Команда systemctl від meshpi (дозволи — polkit). None — успіх, інакше текст помилки.
    Завислий systemd (тайм-аут D-Bus) — теж помилка, а не 500 без пояснення."""
    try:
        r = subprocess.run(["systemctl", *args], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return str(e)[:200]
    return (r.stderr.strip()[:200] or f"код {r.returncode}") if r.returncode else None


def journal(unit, n=50):
    return sh("journalctl", "-u", unit, "-b", "-n", str(n), "--no-pager", "-o", "short")


_cpu_prev: tuple[int, int] | None = None  # (усього, простій) тактів із /proc/stat


def cpu_percent():
    """Завантаження процесора, %: різниця між двома викликами (перший — міряє 0,3 с)."""
    global _cpu_prev

    def read():
        v = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        return sum(v[:8]), v[3] + v[4]  # без guest; idle + iowait

    try:
        if _cpu_prev is None:
            _cpu_prev = read()
            time.sleep(0.3)
        cur = read()
    except (OSError, ValueError, IndexError):
        return None
    dt, di = cur[0] - _cpu_prev[0], cur[1] - _cpu_prev[1]
    _cpu_prev = cur
    return round(100 * (dt - di) / dt, 1) if dt > 0 else None


@app.get("/api/system")
def system():
    def num(path):
        try:
            return float(Path(path).read_text())
        except (OSError, ValueError):
            return None

    t = num("/sys/class/thermal/thermal_zone0/temp")
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines()[:5]:
        k, _, v = line.partition(":")
        mem[k] = int(v.split()[0]) // 1024
    du = shutil.disk_usage("/")
    return jsonify(temp=t / 1000 if t is not None else None, load=os.getloadavg()[0], cpu=cpu_percent(),
                   mem_free=mem.get("MemAvailable"), mem_total=mem.get("MemTotal"),
                   disk_free=du.free // 2**20, disk_total=du.total // 2**20)


_health: tuple[float, dict] = (0.0, {})


@app.get("/api/health")
def health():
    # journalctl і systemctl — окремі процеси на слабкому ARM; кеш 8 с, щоб кілька сторінок їх не множили
    global _health
    if time.monotonic() - _health[0] > 8:
        lines = journal("meshtasticd", 200).splitlines()
        _health = (time.monotonic(), {
            "spi": Path("/dev/spidev0.0").exists(),
            "service": sh("systemctl", "is-active", "meshtasticd").strip(),
            "errors": [x for x in lines if RADIO_ERR.search(x) and not HARMLESS.search(x)][-5:]})
    return jsonify({**_health[1], "connected": cur_iface() is not None})


def chip_cap():
    """SX126X_MAX_POWER або RF95_MAX_POWER із конфігів meshtasticd (22, якщо ключа немає)
    і назва конфігу модуля, де він заданий (lora-e22-400m33s.yaml тощо) — щоб знати підсилювач."""
    cap, radio, seen = 22, None, False
    files = [Path("/etc/meshtasticd/config.yaml"), *sorted(Path("/").glob(CFG_GLOB.lstrip("/")))]  # головний конфіг і config.d
    for f in files:
        try:
            found = CAP_RE.findall(f.read_text())
        except OSError:
            continue
        seen = True
        if found:
            cap, radio = int(found[-1]), f.name
    return (cap if seen else None), radio  # немає жодного конфігу (панель не на Pi) — стелю не вигадуємо


class AgentError(Exception):
    """Агент на Pi недоступний або відповів не так: текст готовий для показу."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # агент не має перенаправляти: не даємо вести запит з токеном деінде
        return None


AGENT_TTL = 15  # с: стільки тримаємо відповідь агента (він сам кешує журнал ще 10 с)


def _num_or_none(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and -100 <= v <= 100 else None


def agent_info(c):
    """Потужність і стеля чипа від агента на Pi (pi-agent/): {logged, final, cap, radio}. Кеш на AGENT_TTL с."""
    ts, val = c.agent_cache
    if val is not None and time.time() - ts < AGENT_TTL:
        return val
    req = urllib.request.Request(c.agent + "/radio.json", headers={"Authorization": "Bearer " + c.agent_token})
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=4) as r:
            raw = json.loads(r.read(4096))
    except urllib.error.HTTPError as e:
        raise AgentError({401: "токен не підходить", 403: "IP сервера панелі не дозволено на агенті",
                          404: "це не агент (шлях /radio.json не знайдено)"}.get(e.code, f"відповідь HTTP {e.code}")) from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise AgentError("немає зв'язку з агентом: " + str(getattr(e, "reason", e))[:80]) from e
    if not isinstance(raw, dict):
        raise AgentError("агент відповів не так, як очікувалось")
    radio = raw.get("radio")
    val = {"logged": _num_or_none(raw.get("logged")), "final": _num_or_none(raw.get("final")), "cap": _num_or_none(raw.get("cap")),
           "radio": radio if isinstance(radio, str) and re.fullmatch(r"[\w.-]{1,80}", radio) else None}
    c.agent_cache = (time.time(), val)
    return val


def build_running():
    return bool(sh("systemctl", "list-units", "meshpi-power@*", "--state=active", "--no-legend").strip())


_pkg: tuple[float, dict] = (-1e9, {})


def package_state():
    """Чи утримується пакет meshtasticd від оновлень (raise-power.sh робить apt-mark hold) і які версії.
    Кеш 10 хв: apt-cache на ARM повільний, а головна сторінка питає /api/power щохвилини;
    після запуску збирання кеш скидається (power_build)."""
    global _pkg
    if time.monotonic() - _pkg[0] > 600:
        policy = sh("apt-cache", "policy", "meshtasticd")
        ver = {k: None if v == "(none)" else v  # «(none)» — пакета чи кандидата немає
               for k, v in re.findall(r"^\s*(Installed|Candidate):\s*(\S+)", policy, re.M)}
        _pkg = (time.monotonic(), {"held": "meshtasticd" in sh("apt-mark", "showhold").split(),
                                   "installed": ver.get("Installed"), "candidate": ver.get("Candidate")})
    return _pkg[1]


@app.get("/api/power")
def power():
    """Потужність радіо. Журнал і конфіги meshtasticd є лише на самій Pi: або читаємо їх тут (панель на Pi), або
    беремо в агента на Pi (pi-agent/), адресу й токен якого задано в налаштуваннях вузла."""
    c, i = cur(), cur_iface()
    exe = shutil.which("meshtasticd")
    source, agent_error, logged, final, cap, radio = None, None, None, None, None, None
    if c.agent:
        try:
            a = agent_info(c)
            logged, final, cap, radio, source = a["logged"], a["final"], a["cap"], a["radio"], "agent"
        except AgentError as e:
            agent_error = str(e)
    else:
        # лише потрібні рядки з журналу за все завантаження: «останні 500» при активному ефірі їх уже не містять
        text = sh("journalctl", "-u", "meshtasticd", "-b", "--no-pager", "-o", "cat",
                  "-g", "Set radio|Final Tx power", "-n", "10", "-r")
        # -n з -g journalctl і так видає новіші першими; -r явно — щоб порядок не залежав від версії systemd
        lg, fn = [int(x) for x in POWER_RE.findall(text)], [int(x) for x in FINAL_RE.findall(text)]
        logged, final = (lg[0] if lg else None), (fn[0] if fn else None)
        cap, radio = chip_cap()
        source = "local" if (exe or logged is not None or cap is not None) else None
    setting = i.localNode.localConfig.lora.tx_power if i else None
    estimate = False
    if logged is None and final is None and setting:
        # рядок «Set radio» живе в журналі лише години (журнал у пам'яті): оцінюємо за заданою потужністю, без ліміту регіону
        logged, estimate = setting, True
    if final is not None:
        effective = final
    else:
        effective = min(logged, cap) if logged is not None and cap is not None else None
    native = bool(exe)  # meshtasticd встановлено тут: «змінена прошивка» і пакети мають сенс лише тоді
    return jsonify(
        setting=setting, estimate=estimate,
        logged=logged, cap=cap, radio=radio, effective=effective, source=source, agent_error=agent_error, agent=bool(c.agent),
        patched=bool(exe and Path(exe + ".stock").exists()) if native else None,
        running=build_running(), **package_state(),
        log=sh("journalctl", "-u", "meshpi-power@*", "-b", "-n", "60", "--no-pager", "-o", "cat"))


@app.post("/api/power-build")
def power_build():
    target = str(body().get("target", ""))
    if not BUILD_RE.match(target):
        return err("target: 1..22 або restore")
    if build_running():
        return err("Збирання вже триває", 409)
    global _pkg
    _pkg = (-1e9, {})  # після збирання чи повернення утримання пакета зміниться
    if e := systemctl("start", "--no-block", f"meshpi-power@{target}.service"):
        return err("Не вдалося запустити: " + e, 500)
    return jsonify(ok=True)


@app.get("/api/log")
def get_log():
    unit = request.args.get("unit", "meshtasticd")
    if unit not in UNITS:
        return err("Невідомий сервіс")
    return jsonify(text=journal(unit))


@app.post("/api/restart")
def restart():
    target = body().get("target")
    cmd = {"meshtasticd": ["restart", "meshtasticd"], "board": ["reboot"],
           "poweroff": ["poweroff"]}.get(target)
    if cmd is None:
        return err("target: meshtasticd, board або poweroff")
    # reboot/poweroff через logind повертаються одразу, тож відмову polkit видно, а відповідь встигає піти
    if e := systemctl(*cmd):
        return err("Не вдалося: " + e, 500)
    return jsonify(ok=True)


@app.post("/api/name")
def set_name():
    d = body()
    n = names(d.get("long"), d.get("short"))
    if n is None:
        return err("Назва: 1..39 байт; коротка назва: 1..4 байти")
    long, short = n
    i = need()
    i.localNode.setOwner(long, short)
    return jsonify(ok=True)


@app.post("/api/auth")
def set_auth():
    """Увімкнути (on: true) або вимкнути (false) пароль на вхід у веб-інтерфейс."""
    on = body().get("on")
    if not isinstance(on, bool):
        return err("on: true або false")
    if on:
        Path(NOAUTHFILE).unlink(missing_ok=True)
    else:
        write_private(NOAUTHFILE, "")
    return jsonify(auth=auth_on())


@app.post("/api/password")
def change_password():
    global _good
    d = body()
    old, new = d.get("old"), d.get("new")
    if not isinstance(old, str) or not isinstance(new, str):
        return err("Поля old і new мають бути рядками")
    try:
        new.encode()
        old_ok = pw_ok(old)
    except UnicodeEncodeError:
        return err("Некоректний пароль")
    if not old_ok:
        return err("Старий пароль неправильний", 403)
    if not 8 <= len(new) <= 128:
        return err("Новий пароль: 8..128 символів")
    salt = os.urandom(16)
    write_private(PWFILE, f"{salt.hex()}${_scrypt(new, salt)}")
    _good = None
    session["stamp"] = _auth_stamp()  # цей браузер лишається ув'язаним, решту сесій буде скинуто
    return jsonify(ok=True)


@app.get("/api/history")
def history():
    try:
        hours = min(max(int(request.args.get("hours", 24)), 1), 168)
    except ValueError:
        return err("hours: число")
    with db_lock:
        rows = db.execute("SELECT t, chan, air, nodes FROM metric WHERE owner = ? AND t >= ? ORDER BY t",
                          (cur_id(), int(time.time()) - hours * 3600)).fetchall()
    return unchanged_304(jsonify([{"t": t, "chan": c, "air": a, "nodes": n} for t, c, a, n in rows]))


@app.get("/api/backup")
def backup():
    """Один JSON: канали з ключами (URL), назва вузла, історія. Є ключі каналів — зберігайте таємно."""
    i = need()
    user = (i.getMyNodeInfo() or {}).get("user", {})
    with db_lock:
        msgs = db.execute(f"SELECT {MSG_COLS} FROM msg WHERE owner = ? ORDER BY id", (cur_id(),)).fetchall()
        metrics = db.execute("SELECT t, chan, air, nodes FROM metric WHERE owner = ? ORDER BY t", (cur_id(),)).fetchall()
    data = {"version": 1, "url": i.localNode.getURL(includeAll=True), "long": user.get("longName"),
            "short": user.get("shortName"), "msgs": msgs, "metrics": metrics}
    return Response(json.dumps(data), mimetype="application/json", headers={
        "Content-Disposition": f'attachment; filename="meshpi-{time.strftime("%Y%m%d-%H%M")}.json"'})


def _int(v, lo, hi):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError
    return v


def _opt(v, kind):
    if v is not None and (isinstance(v, bool) or not isinstance(v, kind)):
        raise ValueError
    return v


def _msg_row(r):
    if len(r) == 8:  # копія попередньої версії: без id пакета й реакцій
        r = [*r, None, None, 0]
    t, sender, name, dest, ch, text, snr, out, pid, reply, emoji = r
    if not isinstance(text, str) or len(text) > 1000:
        raise ValueError
    pid, reply = (None if v is None else _int(v, 0, 2**32 - 1) for v in (pid, reply))
    return (_int(t, 0, 2**40), _opt(sender, str), _opt(name, str), _opt(dest, str), _int(ch, 0, 7), text,
            _opt(snr, (int, float)), _int(out, 0, 1), pid, reply, _int(emoji, 0, 1))


def _met_row(r):
    t, chan, air, nodes = r
    return _int(t, 0, 2**40), _opt(chan, (int, float)), _opt(air, (int, float)), _int(nodes, 0, 10**6)


@app.post("/api/restore")
def restore():
    d = body()
    url, msgs, mets = d.get("url"), d.get("msgs"), d.get("metrics")
    long, short = d.get("long"), d.get("short")
    if d.get("version") != 1 or not isinstance(url, str) or not isinstance(msgs, list) \
            or not isinstance(mets, list):
        return err("Це не файл резервної копії meshpi")
    if not url.startswith("https://meshtastic.org/e/#") or len(msgs) > 5 * KEEP or len(mets) > 10000:
        return err("Некоректний файл резервної копії")
    try:
        _, b64 = parse_channel_url(url)  # бібліотека на зіпсованому base64 впала б уже після setOwner
        msg_rows = [_msg_row(r) for r in msgs]
        met_rows = [_met_row(r) for r in mets]
    except (TypeError, ValueError, OverflowError):
        return err("Некоректний файл резервної копії")
    i = need()
    if i.localNode.channels is None:  # setURL бібліотеки інакше викликає sys.exit
        return err("Канали вузла ще не завантажені — спробуйте за хвилину", 503)
    n = names(long, short)
    if n:
        i.localNode.setOwner(*n)  # до setURL: після запису LoRa вузол перезапускається
    i.localNode.setURL("https://meshtastic.org/e/#" + b64)  # канали, ключі й налаштування LoRa
    owner = cur_id()
    try:
        with db_lock, db:
            db.execute("DELETE FROM msg WHERE owner = ?", (owner,))
            db.executemany(f"INSERT INTO msg ({MSG_COLS}, owner) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           [(*r, owner) for r in msg_rows[-KEEP:]])
            db.executemany("INSERT OR REPLACE INTO metric (t, chan, air, nodes, owner) VALUES (?,?,?,?,?)",
                           [(*r, owner) for r in met_rows])
    except sqlite3.Error:
        log.exception("не вдалося відновити історію")
        return err("Канали відновлено, але історію записати не вдалося", 500)
    return jsonify(ok=True)


@app.get("/api/nodes")
def nodes():
    i, owner = cur_iface(), cur_id()
    out = []
    all_n = all_nodes(i)
    env = env_latest(all_n)
    for n in all_n:
        u, p = n.get("user", {}), n.get("position", {})
        nid = u.get("id") or (f"!{n['num']:08x}" if "num" in n else None)
        e = env.get(nid or "")
        out.append({
            "id": nid, "long": u.get("longName"), "short": u.get("shortName"),
            "hw": u.get("hwModel"), "snr": n.get("snr"), "heard": n.get("lastHeard"),
            "hops": n.get("hopsAway"), "lat": p.get("latitude"), "lon": p.get("longitude"),
            "rssi": rssi.get((owner, nid or "")),
            "env": e[0] if e else None, "env_t": e[1] if e else None,
            "mqtt": bool(n.get("viaMqtt")),  # вузол відомий через MQTT-міст, а не з радіоефіру
            "fav": bool(n.get("isFavorite")), "ignored": bool(n.get("isIgnored")), "muted": bool(n.get("isMuted")),
            "role": u.get("role") or "CLIENT",
        })
    out.sort(key=lambda x: x["heard"] or 0, reverse=True)
    return unchanged_304(jsonify(out))


@app.get("/api/mesh")
def api_mesh():
    """Граф мережі за добу (?day=РРРР-ММ-ДД, типово сьогодні) і перелік діб з даними."""
    flush_links(True)
    day = request.args.get("day") or day_of()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return err("day: РРРР-ММ-ДД")
    with_mqtt = request.args.get("mqtt") == "1"
    i, owner = cur_iface(), cur_id()
    with db_lock:
        days = db.execute("SELECT day, SUM(n), COUNT(*) FROM link WHERE owner = ? GROUP BY day ORDER BY day DESC",
                          (owner,)).fetchall()
        rows = db.execute("SELECT src, dst, flow, kind, n, snr_sum, snr_n, t FROM link WHERE owner = ? AND day = ?",
                          (owner, day)).fetchall()
    known = {}
    for n in all_nodes(i):
        nid = (n.get("user") or {}).get("id") or (node_id(n["num"]) if isinstance(n.get("num"), int) else None)
        if nid:
            known[nid] = n
    me = ((i.getMyNodeInfo() or {}).get("user") or {}).get("id") if i else None
    hidden = set() if with_mqtt else {nid for nid, n in known.items() if n.get("viaMqtt")}
    links = [{"a": a, "b": b, "flow": flow, "kind": kind, "n": n, "t": t,
              "snr": round(ss / sn, 1) if sn else None}
             for a, b, flow, kind, n, ss, sn, t in rows if a not in hidden and b not in hidden]
    ids = {x for l in links for x in (l["a"], l["b"])} | ({me} if me else set())
    repeaters = {l["a"] for l in links if l["kind"] == "relay"} | {l["b"] for l in links if l["kind"] == "via"}
    nodes = []
    for nid in sorted(ids):
        n = known.get(nid) or {}
        u, p = n.get("user") or {}, n.get("position") or {}
        role = u.get("role") or "CLIENT"
        nodes.append({"id": nid, "name": u.get("longName") or nid, "short": u.get("shortName"), "role": role,
                      "repeater": role in ROUTER_ROLES or nid in repeaters, "me": nid == me,
                      "hw": u.get("hwModel"), "snr": n.get("snr"), "rssi": rssi.get((owner, nid)),
                      "hops": n.get("hopsAway"), "mqtt": bool(n.get("viaMqtt")), "heard": n.get("lastHeard"),
                      "lat": p.get("latitude"), "lon": p.get("longitude")})
    mine = next((n for n in nodes if n["me"] and n["lat"] is not None and n["lon"] is not None), None)
    origin = {"lat": mine["lat"], "lon": mine["lon"]} if mine else home()  # центр кілець відстані
    return jsonify({"day": day, "today": day_of(), "me": me, "origin": origin, "nodes": nodes, "links": links,
                    "days": [{"day": d, "packets": s, "links": c} for d, s, c in days]})


# ---- запити до іншого вузла (як «Подробиці» в застосунку) ----
ASK = {"user", "trace", "signal", "device"}
ASK_GAP = 30  # с між однаковими запитами до вузла: ефір спільний
asked: dict[tuple, dict[str, dict]] = {}  # (підключення, вузол) -> вид -> {sent, done, ok, data, error}
UNK_SNR = -128


def _hop(num, snr=None, i=None):
    nid = f"!{num:08x}"
    return {"id": nid, "name": node_name(i, nid) if i else nid,
            "snr": None if snr is None or snr == UNK_SNR else snr / 4}


def _route(start, mid, end, snrs, i=None):
    """[старт, проміжні…, кінець]; SNR на кожному кроці (у пакеті — у чвертях дБ)."""
    snrs = list(snrs) if len(snrs) == len(mid) + 1 else [None] * (len(mid) + 1)
    return [_hop(start, None, i)] + [_hop(n, s, i) for n, s in zip([*mid, end], snrs, strict=True)]


def ask_result(kind, p, i=None):
    """Відповідь вузла -> дані для сторінки; помилка маршрутизації (немає відповіді тощо) -> error."""
    d = p.get("decoded", {})
    if d.get("portnum") == "ROUTING_APP":
        return {"ok": False, "error": d.get("routing", {}).get("errorReason", "?")}
    out = {"ok": True, "snr": p.get("rxSnr"), "rssi": p.get("rxRssi"),
           "hops": (p["hopStart"] - p.get("hopLimit", 0)) if "hopStart" in p else None}
    if kind == "user":
        u = d.get("user", {})
        out["data"] = {"long": u.get("longName"), "short": u.get("shortName"), "hw": u.get("hwModel"),
                       "role": u.get("role", "CLIENT")}
    elif kind == "device":
        out["data"] = d.get("telemetry", {}).get("deviceMetrics", {})
    elif kind == "trace":
        r = mesh_pb2.RouteDiscovery.FromString(d.get("payload", b""))
        out["data"] = {"towards": _route(p["to"], r.route, p["from"], r.snr_towards, i),
                       "back": _route(p["from"], r.route_back, p["to"], r.snr_back, i) if r.snr_back else None}
    return out


@app.post("/api/ask")
def ask():
    d = body()
    to, kind = d.get("to"), d.get("kind")
    if not isinstance(to, str) or not re.fullmatch(r"![0-9a-f]{8}", to) or kind not in ASK:
        return err("to: !xxxxxxxx; kind: user | trace | signal | device")
    i = need()
    prev = asked.get((owner_of(i), to), {}).get(kind)
    if prev and time.time() - prev["sent"] < ASK_GAP:
        return err(f"Запит уже надіслано — повторити можна через {ASK_GAP} с", 429)
    start_ask(i, to, kind)
    return jsonify(ok=True)


def start_ask(i, to, kind, done=None):
    """Надіслати запит вузлу; відповідь запишеться в `asked` (і в архів зв'язків). done (Event) — подати, коли вона прийде."""
    owner = owner_of(i)
    if len(asked) >= 100 and (owner, to) not in asked:  # пам'ять обмежена: забуваємо найстаріший вузол
        asked.pop(next(iter(asked)))
    entry = {"sent": time.time(), "done": None}
    asked.setdefault((owner, to), {})[kind] = entry

    def on_response(p):
        entry.update(ask_result(kind, p, i), done=time.time())
        if kind in PROBES:
            save_probe(to, kind, entry, owner)
        if kind == "trace" and entry.get("ok"):
            _quiet(lambda: record_trace(entry["data"], owner))
        if done:
            done.set()

    if kind == "user":
        me = (i.getMyNodeInfo() or {}).get("user", {})
        data, port = mesh_pb2.User(id=me.get("id", ""), long_name=me.get("longName", ""),
                                   short_name=me.get("shortName", "")), portnums_pb2.PortNum.NODEINFO_APP
    elif kind == "trace":
        data, port = mesh_pb2.RouteDiscovery(), portnums_pb2.PortNum.TRACEROUTE_APP
    else:  # signal і device: запит показників пристрою; для signal важливі SNR/RSSI відповіді
        data, port = telemetry_pb2.Telemetry(device_metrics=telemetry_pb2.DeviceMetrics()), \
            portnums_pb2.PortNum.TELEMETRY_APP
    i.sendData(data, destinationId=to, portNum=port, wantResponse=True, onResponse=on_response,
               hopLimit=i.localNode.localConfig.lora.hop_limit or 3)
    return entry


PROBES, PROBES_KEEP = ("trace", "signal"), 3  # які відповіді пам'ятати й скільки останніх на вузол


def save_probe(node, kind, entry, owner=None):
    """Відповідь на «Маршрут»/«Сигнал» — в історію вузла (переживає перезапуск); лишаються 3 останні."""
    owner = owner or cur_id()
    try:
        with db_lock, db:
            db.execute("INSERT INTO probe (node, kind, t, res, owner) VALUES (?,?,?,?,?)",
                       (node, kind, entry["done"], json.dumps(entry), owner))
            db.execute("DELETE FROM probe WHERE owner = ? AND node = ? AND kind = ? AND rowid NOT IN (SELECT rowid"
                       " FROM probe WHERE owner = ? AND node = ? AND kind = ? ORDER BY t DESC LIMIT ?)",
                       (owner, node, kind, owner, node, kind, PROBES_KEEP))
    except sqlite3.Error:
        log.exception("не вдалося зберегти результат перевірки")


@app.get("/api/ask")
def ask_results():
    to, owner = request.args.get("to", ""), cur_id()
    with db_lock:
        rows = db.execute("SELECT kind, res FROM probe WHERE owner = ? AND node = ? ORDER BY t DESC",
                          (owner, to)).fetchall()
    history: dict[str, list] = {k: [] for k in PROBES}
    for k, res in rows:
        history[k].append(json.loads(res))
    return jsonify({**asked.get((owner, to), {}), "history": history})


# ---- автоматичні маршрути: «Маршрут» по черзі до вузлів раз на 3 год і для щойно з'явилися ----
# Кожен запит — передача в ефір, тому: лише по одному (наступний — після відповіді або таймауту), з паузою між
# ними, без MQTT-вузлів і лише до недавно чутих і близьких вузлів. Вимикається прапорцем на вкладці «Мережа».
NOTRACEFILE = os.environ.get("MESHPI_NOTRACEFILE", str(Path(DB_PATH).with_name("notrace")))  # є файл — вимкнено
TRACE_EVERY = int(os.environ.get("MESHPI_TRACE_EVERY", str(3 * 3600)))  # с між повними колами
TRACE_WAIT = 150           # с чекати відповідь одного вузла, далі йдемо до наступного
TRACE_PAUSE = 45           # с між запитами: ефір спільний
TRACE_MAX_HOPS = int(os.environ.get("MESHPI_TRACE_HOPS", "5"))   # далі за стільки стрибків не питаємо
TRACE_MAX_AGE = 86400      # с: питаємо лише вузли, чуті за останню добу
trace_ctx: dict[str, dict] = {}  # стан планувальника кожного підключення: черга, поточний, наступне коло…


def tctx(owner):
    return trace_ctx.setdefault(owner, {"queue": collections.deque(), "current": None, "next_cycle": 0.0,
                                        "last": None, "seen": None})


def trace_eligible(i):
    """Вузли, яким має сенс слати «Маршрут»: радіо (не MQTT), не ми, близько, недавно чутні."""
    me = ((i.getMyNodeInfo() or {}).get("user") or {}).get("id")
    now, out = time.time(), []
    for n in all_nodes(i):
        nid = (n.get("user") or {}).get("id") or (node_id(n["num"]) if isinstance(n.get("num"), int) else None)
        hops = n.get("hopsAway")
        if (not nid or nid == me or n.get("viaMqtt") or (hops is not None and hops > TRACE_MAX_HOPS)
                or now - (n.get("lastHeard") or 0) > TRACE_MAX_AGE):
            continue
        out.append((hops if hops is not None else 99, -(n.get("lastHeard") or 0), nid))
    return [nid for _h, _t, nid in sorted(out)]


def trace_tick(i):
    """Один крок планувальника: поповнити чергу (коло раз на TRACE_EVERY, нові вузли — першими)."""
    now = time.time()
    ctx = tctx(owner_of(i))
    queue = ctx["queue"]
    ids = trace_eligible(i)
    everyone = {nid for nid in ids}
    seen = ctx["seen"]
    if seen is None:  # перший запуск: вузли, що вже є, — не «нові»; перше коло через 2 хв
        ctx["seen"], ctx["next_cycle"] = set(everyone), now + 120
        return
    for nid in ids:
        if nid not in seen and nid not in queue:
            queue.appendleft(nid)  # новий вузол — без черги
    seen |= everyone
    if now >= ctx["next_cycle"]:
        ctx["next_cycle"] = now + TRACE_EVERY
        queue.extend(nid for nid in ids if nid not in queue)


def trace_one(i, nid):
    done = threading.Event()
    owner = owner_of(i)
    ctx = tctx(owner)
    prev = asked.get((owner, nid), {}).get("trace")
    if prev and time.time() - prev["sent"] < ASK_GAP:
        return  # щойно питали (наприклад, вручну)
    ctx["current"] = nid
    try:
        start_ask(i, nid, "trace", done)
        ok = done.wait(TRACE_WAIT)
    except Exception:  # noqa: BLE001 — збій відправки не зупиняє чергу
        log.exception("автомаршрут до %s не надіслано", nid)
        ok = False
    finally:
        ctx["current"] = None
    entry = asked.get((owner, nid), {}).get("trace") or {}
    ctx["last"] = {"id": nid, "ok": bool(ok and entry.get("ok")), "t": int(time.time())}


def autotrace():
    """Планувальник усіх підключень по колу: один запит за раз на весь ефір панелі."""
    while True:
        time.sleep(15)
        if Path(NOTRACEFILE).exists() or scan["state"] in ("running", "restoring"):
            continue
        for c in list(conns.values()):
            i = c.iface
            if i is None:
                continue
            try:
                trace_tick(i)
                queue = tctx(c.id)["queue"]
                if queue:
                    trace_one(i, queue.popleft())
                    time.sleep(TRACE_PAUSE)
            except Exception:  # noqa: BLE001
                log.exception("збій планувальника автомаршрутів")


@app.get("/api/autotrace")
def get_autotrace():
    ctx, i = tctx(cur_id()), cur_iface()
    last = ctx["last"]
    return jsonify(on=not Path(NOTRACEFILE).exists(), current=ctx["current"], queued=len(ctx["queue"]),
                   next_cycle=int(ctx["next_cycle"]) or None, every=TRACE_EVERY, max_hops=TRACE_MAX_HOPS,
                   last=dict(last, name=node_name(i, last["id"]) if i else last["id"]) if last else None)


@app.post("/api/autotrace")
def set_autotrace():
    on = body().get("on")
    if not isinstance(on, bool):
        return err("on: true або false")
    if on:
        Path(NOTRACEFILE).unlink(missing_ok=True)
    else:
        write_private(NOTRACEFILE, "")
        for ctx in trace_ctx.values():
            ctx["queue"].clear()
    return jsonify(on=on)


@app.post("/api/forget")
def forget():
    """Видалити приватну розмову з вузлом: усе, що ми йому надіслали особисто, і що він — нам особисто."""
    peer = body().get("peer")
    if not isinstance(peer, str) or not re.fullmatch(r"![0-9a-f]{8}", peer):
        return err("peer: !xxxxxxxx")
    try:
        with db_lock, db:  # те саме правило, що dmPeer у logic.js
            n = db.execute("DELETE FROM msg WHERE owner = ? AND ((out = 1 AND dest = ?) OR (out = 0 AND sender = ?"
                           " AND dest IS NOT NULL AND dest != '^all'))", (cur_id(), peer, peer)).rowcount
    except sqlite3.Error:
        log.exception("не вдалося видалити розмову")
        return err("Не вдалося видалити історію", 500)
    return jsonify(deleted=n)


@app.get("/api/messages")
def get_messages():
    return unchanged_304(jsonify(recent()))


@app.post("/api/send")
def send():
    d = body()
    # немає поля — усім; а null чи "" — помилка, а не тиха розсилка в канал замість особистого
    text, to = d.get("text"), d.get("to", "^all")
    if not isinstance(text, str) or not isinstance(to, str):
        return err("Поля text і to мають бути рядками")
    text = text.strip()
    try:
        ch = int(d.get("ch", 0))
        size = len(text.encode())
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError):  # UnicodeEncodeError: сурогат з JSON
        return err("Некоректний канал або текст")
    if not text or size > MAX_TEXT:
        return err(f"Текст порожній або довший за {MAX_TEXT} байт")
    if not DEST_RE.match(to) or not 0 <= ch <= 7:
        return err("Некоректний адресат або канал")
    reply, emoji = d.get("reply"), d.get("emoji", False)
    try:
        if reply is not None:
            _int(reply, 1, 2**32 - 1)
        if not isinstance(emoji, bool) or emoji and (reply is None or size > 32):
            raise ValueError
    except ValueError:
        return err("Реакція: reply — id повідомлення, emoji — true/false, текст — один емодзі")
    i = need()
    owner, track = owner_of(i), to != "^all" and not emoji  # доставку стежимо лише для особистих повідомлень

    def on_ack(pk):
        dec = pk.get("decoded", {})
        if dec.get("routing", {}).get("errorReason", "NONE") != "NONE":
            status = 3  # відмова: немає маршруту, вузол не підтвердив після повторів
        else:  # підтвердження від адресата — доставлено; від нашого ж вузла — лише чутно ретрансляцію сусіда
            frm = pk.get("fromId") or (node_id(pk["from"]) if isinstance(pk.get("from"), int) else None)
            status = 2 if frm == to else 1
        set_ack(owner, dec.get("requestId"), status)

    p = send_text(i, text, to, ch, reply, emoji, on_ack if track else None)
    add_msg(None, "Ви", to, ch, text, None, 1, getattr(p, "id", None) or None, reply, int(emoji), owner=owner,
            ack=0 if track else None)
    return jsonify(ok=True)


def send_text(i, text, to, ch, reply=None, emoji=False, on_ack=None):
    """Текст або реакція-емодзі на повідомлення reply (як «вподобати» в застосунку). Повертає пакет з id.
    У бібліотеки немає параметра emoji — пакет збирається тут, як у sendData (тест стежить за бібліотекою).
    on_ack — стежити за доставкою (потрібне підтвердження; sendText бібліотеки не віддає ACK обробнику)."""
    if not emoji and on_ack:
        return i.sendData(text.encode(), destinationId=to, portNum=portnums_pb2.PortNum.TEXT_MESSAGE_APP, wantAck=True,
                          onResponse=on_ack, onResponseAckPermitted=True, channelIndex=ch, replyId=reply)
    if not emoji:
        return i.sendText(text, destinationId=to, channelIndex=ch, replyId=reply)
    p = mesh_pb2.MeshPacket(channel=ch)
    p.decoded.portnum = portnums_pb2.PortNum.TEXT_MESSAGE_APP
    p.decoded.payload, p.decoded.reply_id, p.decoded.emoji = text.encode(), reply, 1
    p.id = i._generatePacketId()
    return i._sendPacket(p, to)


def parse_channel_url(url):
    """ChannelSet з посилання https://meshtastic.org/e/#… (або …/e/?add=true#…); інакше ValueError."""
    if not isinstance(url, str) or len(url) > 2000 or "#" not in url or "meshtastic.org/e/" not in url:
        raise ValueError
    b64 = url.split("#", 1)[1].strip()
    try:
        cs = apponly_pb2.ChannelSet.FromString(base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)))
    except (binascii.Error, DecodeError) as e:
        raise ValueError from e
    if not cs.settings:
        raise ValueError
    return cs, b64


@app.post("/api/channel-url")
def channel_url():
    """Канали з посилання/QR: типово додає нові (вторинні, LoRa не чіпає); replace — як «Замінити»."""
    d = body()
    try:
        cs, b64 = parse_channel_url(d.get("url"))
    except ValueError:
        return err("Це не посилання на канали Meshtastic (https://meshtastic.org/e/#…)")
    i = need()
    node = i.localNode
    if node.channels is None:  # getChannelByName/setURL бібліотеки без каналів падають
        return err("Канали вузла ще не завантажені — спробуйте за хвилину", 503)
    if d.get("replace") is True:
        # бібліотека розбирає лише форму «/#»; вузол перезапуститься (у посиланні й налаштування LoRa)
        node.setURL("https://meshtastic.org/e/#" + b64)
        return jsonify(added=[s.name or "(основний)" for s in cs.settings], skipped=[])
    added, skipped = [], []
    for chs in cs.settings:  # як addOnly у бібліотеці, але без перезапису LoRa налаштуваннями з посилання
        ch = None if not chs.name or node.getChannelByName(chs.name) else node.getDisabledChannel()
        if ch is None:
            skipped.append(chs.name or "(без назви)")
            continue
        ch.settings.CopyFrom(chs)
        ch.role = channel_pb2.Channel.Role.SECONDARY
        node.writeChannel(ch.index)
        added.append(chs.name)
    return jsonify(added=added, skipped=skipped)


@app.post("/api/radio")
def radio():
    d = body()
    try:
        region = LoRa.RegionCode.Value(d["region"])
        preset = LoRa.ModemPreset.Value(d["preset"])
        tx = int(d.get("tx_power", 0))
        hops = int(d.get("hop_limit", 3))
        slot = int(d.get("channel_num", 0))
    except (KeyError, ValueError, TypeError, OverflowError):  # OverflowError: 1e999 → inf
        return err("Некоректний регіон, пресет або потужність")
    if not 0 <= tx <= 22:  # 22 дБм — максимум SX1268
        return err("Потужність має бути 0..22 дБм (0 = за замовчуванням регіону)")
    if not 1 <= hops <= 7:
        return err("Hop limit має бути 1..7")
    if not 0 <= slot <= 255:
        return err("Слот частоти має бути 0..255 (0 = автоматично)")
    if scan["state"] in ("running", "restoring"):
        return err("Іде сканування пресетів — радіо зараз змінювати не можна", 409)
    i = need()
    lora = i.localNode.localConfig.lora
    lora.channel_num = slot
    lora.region, lora.use_preset, lora.modem_preset, lora.tx_power = region, True, preset, tx
    lora.hop_limit = hops
    i.localNode.writeConfig("lora")  # вузол після цього перезапускається
    return jsonify(ok=True)


def scan_hear(packet, interface):
    """Запис пакета, почутого на поточному пресеті сканування (свої пакети й без відправника — ні)."""
    sender = sender_of(packet)
    if sender is None or sender == (interface.getMyNodeInfo() or {}).get("user", {}).get("id"):
        return
    r = scan["results"][scan["presets"][scan["cur"]]]
    r["packets"] += 1
    n = r["nodes"].setdefault(sender, {"name": node_name(interface, sender), "snr": None, "direct": False})
    snr = packet.get("rxSnr")
    if snr is not None and (n["snr"] is None or snr > n["snr"]):
        n["snr"] = snr
    hs = packet.get("hopStart")
    n["direct"] = n["direct"] or bool(hs) and packet.get("hopLimit") == hs  # без hop-ів — почули напряму


def set_preset(name, c):
    """Міняє пресет вузла підключення c і чекає, поки він перезапуститься, а з'єднання з ним повернеться."""
    i = c.iface
    if i is None:
        raise Offline
    lora = i.localNode.localConfig.lora
    value = LoRa.ModemPreset.Value(name)
    if lora.use_preset and lora.modem_preset == value:
        return
    lora.use_preset, lora.modem_preset = True, value
    i.localNode.writeConfig("lora")
    t = time.time()
    while c.iface is i and time.time() - t < 30:  # спершу розрив старого з'єднання
        time.sleep(1)
    if c.iface is i:  # не розірвалось — прошивка застосувала без перезапуску
        time.sleep(5)
        return
    while time.time() - t < SCAN_SETTLE:  # потім нове з'єднання
        j = c.iface
        if j is not None:
            if j.localNode.localConfig.lora.modem_preset != value:
                raise RuntimeError(f"вузол не прийняв пресет {name}")
            time.sleep(5)  # радіо після старту ще налаштовується
            return
        time.sleep(1)
    raise TimeoutError(f"вузол не повернувся після зміни пресета на {name}")


def scan_run(presets, dwell, c):
    """Потік сканування вузла підключення c: по черзі слухає кожен пресет dwell секунд, наприкінці повертає початковий."""
    error = None
    try:
        if c.iface is None:
            raise Offline
        orig = _name(LoRa.ModemPreset, c.iface.localNode.localConfig.lora.modem_preset)
        write_private(SCANFILE, f"{c.id}\n{orig}")
        for k, name in enumerate(presets):
            scan.update(cur=k, listening=False)
            set_preset(name, c)
            scan.update(ends=time.time() + dwell, listening=True)
            scan_stop.wait(dwell)
            scan["listening"] = False
            if scan_stop.is_set():
                break
    except Exception as e:  # noqa: BLE001
        error = str(e)
        log.exception("сканування пресетів перервано")
    scan.update(listening=False, state="restoring")
    try:
        if Path(SCANFILE).exists():
            set_preset(orig, c)
            Path(SCANFILE).unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001
        error = (error + "; " if error else "") + f"не вдалося повернути пресет ({e}) — поверніть вручну"
        log.exception("не вдалося повернути пресет")
    scan.update(state="error" if error else "stopped" if scan_stop.is_set() else "done", error=error)


def scan_recover():
    """Після збою посеред сканування (перезапуск вебу) — повернути пресет із файлу."""
    try:
        text = Path(SCANFILE).read_text().strip()
    except OSError:
        return
    cid, _, orig = text.rpartition("\n")  # «підключення\nпресет»; старий формат — лише пресет вузла «main»
    c = conns.get(cid or "main") or next(iter(conns.values()))
    while c.iface is None and not c.dead:
        time.sleep(2)
    try:
        set_preset(orig, c)
        Path(SCANFILE).unlink(missing_ok=True)
        log.info("після перерваного сканування повернуто пресет %s", orig)
    except Exception:  # noqa: BLE001
        log.exception("не вдалося повернути пресет %s після перерваного сканування", orig)


@app.get("/api/scan")
def scan_status():
    return jsonify(scan)


@app.post("/api/scan")
def scan_start():
    d = body()
    if d.get("stop"):
        scan_stop.set()
        return jsonify(ok=True)
    presets, dwell = d.get("presets"), d.get("dwell")
    if not isinstance(presets, list) or not presets or len(set(map(str, presets))) != len(presets) \
            or any(p not in LoRa.ModemPreset.keys() for p in presets):
        return err("Оберіть один чи кілька пресетів")
    if type(dwell) is not int or not 30 <= dwell <= 3600:
        return err("Час прослуховування має бути 30..3600 с")
    need()
    c = cur()
    with scan_lock:
        if scan["state"] in ("running", "restoring"):
            return err("Сканування вже триває", 409)
        scan_stop.clear()
        scan.clear()
        scan.update(state="running", conn=c.id, presets=presets, dwell=dwell, cur=0, listening=False, ends=None,
                    error=None, results={p: {"packets": 0, "nodes": {}} for p in presets})
    threading.Thread(target=scan_run, args=(presets, dwell, c), daemon=True).start()
    return jsonify(ok=True)


# ---- список підключених вузлів і перемикач ----
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


@app.get("/api/conns")
def get_conns():
    return jsonify(current=cur_id(), conns=[c.public() for c in conns.values()])


@app.post("/api/conns")
def edit_conns():
    """action: add {name, host, port} | update {id, name, host, port} | remove {id} | select {id}."""
    d = body()
    action = d.get("action")
    if action == "select":
        cid = d.get("id")
        if cid not in conns:
            return err("Невідоме підключення", 404)
        session["node"] = cid
        return jsonify(current=cid)
    if action not in ("add", "update", "remove"):
        return err("action: add | update | remove | select")
    items = {c.id: {"id": c.id, "name": c.name, "host": c.host, "port": c.port, "agent": c.agent, "agent_token": c.agent_token}
             for c in conns.values()}
    if action == "remove":
        cid = d.get("id")
        if cid not in items:
            return err("Невідоме підключення", 404)
        if len(items) == 1:
            return err("Не можна видалити останнє підключення")
        del items[cid]
    else:
        name, host = d.get("name"), d.get("host")
        port = d.get("port", 4403)
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 40:
            return err("Назва: від 1 до 40 символів")
        if not isinstance(host, str) or not HOST_RE.match(host.strip()):
            return err("Адреса: ім'я хоста або IP (літери, цифри, крапка, дефіс)")
        if type(port) is not int or not 1 <= port <= 65535:
            return err("Порт: число 1..65535")
        name, host = name.strip(), host.strip()
        agent, token = d.get("agent"), d.get("agent_token")  # None — не змінювати (для нового — порожньо)
        if agent is not None:
            agent = agent.strip().rstrip("/") if isinstance(agent, str) else None
            if agent is None or (agent and not agent_url_ok(agent)):
                return err("Адреса агента: http://хост:порт (без логіна, шляху й параметрів)")
        if token is not None:
            token = token.strip() if isinstance(token, str) else None
            if token is None or (token and not AGENT_TOKEN_RE.match(token)):
                return err("Токен агента: 16–200 символів (літери, цифри та . _ ~ + / = -)")
        if action == "add":
            base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:20] or "node"
            cid, n = base, 2
            while cid in items:  # унікальний id: назва, назва-2, назва-3…
                cid, n = f"{base}-{n}", n + 1
            if len(items) >= 8:
                return err("Не більше 8 вузлів")
            items[cid] = {"id": cid, "name": name, "host": host, "port": port, "agent": agent or "", "agent_token": token or ""}
        else:
            cid = d.get("id")
            if cid not in items:
                return err("Невідоме підключення", 404)
            items[cid].update(name=name, host=host, port=port)
            if agent is not None:
                items[cid]["agent"] = agent
                if not agent:
                    items[cid]["agent_token"] = ""  # без агента токен не потрібен
            if token is not None:
                items[cid]["agent_token"] = token
        if items[cid]["agent"] and not items[cid]["agent_token"]:
            return err("Для агента потрібен токен (його показує інсталятор на Pi)")
    try:
        write_private(NODES_FILE, json.dumps(list(items.values()), ensure_ascii=False, indent=1))
    except OSError:
        log.exception("не вдалося записати %s", NODES_FILE)
        return err("Не вдалося зберегти список вузлів", 500)
    load_conns()
    return jsonify(current=cur_id(), conns=[c.public() for c in conns.values()])


@app.get("/api/channel-qr")
def channel_qr():
    """QR-код і текст посилання на канали вибраного вузла (як «Поділитися каналом» у застосунку).
    Посилання містить ключі каналів: показуйте його лише тим, кому довіряєте."""
    i = need()
    try:
        url = admin.share_url(i, request.args.get("all") != "0")["url"]
    except admin.AdminError as e:
        return err(str(e), 503)
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=5, border=3, dark="#000000", light="#ffffff", xmldecl=False)
    return jsonify(url=url, qr="data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode())


# ---- точки на карті (waypoints) ----
# Точка — id, координати, назва, опис, значок-емодзі, строк дії (epoch; expire = 0 у пакеті означає «видалити»).
# Приймаємо з ефіру, показуємо на карті у «Вузлах»; свої надсилаємо на вибраний канал. «locked» — номер вузла,
# якому одному дозволено змінювати й видаляти точку (як у застосунку).
WP_NEVER = 2**31 - 1        # «без строку» (0 зарезервовано під видалення)
WP_MAX = 200                # скільки точок тримати на підключення
WP_ICON = 0x1F4CD           # 📍


def wp_cleanup(owner, now):
    db.execute("DELETE FROM waypoint WHERE owner = ? AND expire <= ?", (owner, now))
    db.execute("DELETE FROM waypoint WHERE owner = ? AND id NOT IN (SELECT id FROM waypoint WHERE owner = ?"
               " ORDER BY t DESC LIMIT ?)", (owner, owner, WP_MAX))


def record_waypoint(packet, owner=None):
    """Точка з ефіру: нова чи оновлення (лише від власника, якщо вона заблокована), expire = 0 — видалення."""
    owner = owner or cur_id()
    dec = packet.get("decoded") or {}
    if dec.get("portnum") != "WAYPOINT_APP" or packet.get("viaMqtt"):
        return
    w, sender, snum = dec.get("waypoint") or {}, sender_of(packet), packet.get("from")
    wid = w.get("id")
    if not isinstance(wid, int) or not sender:
        return
    now, expire = int(time.time()), int(w.get("expire") or 0)
    lat, lon = w.get("latitudeI"), w.get("longitudeI")
    try:
        with db_lock, db:
            row = db.execute("SELECT locked FROM waypoint WHERE owner = ? AND id = ?", (owner, wid)).fetchone()
            if row and row[0] and row[0] != snum:
                return  # заблокована за іншим вузлом: чужі зміни ігноруємо
            if expire == 0 or expire <= now:
                db.execute("DELETE FROM waypoint WHERE owner = ? AND id = ?", (owner, wid))
                return
            if not isinstance(lat, int) or not isinstance(lon, int) or not (-900000000 <= lat <= 900000000 and -1800000000 <= lon <= 1800000000):
                return
            db.execute("INSERT OR REPLACE INTO waypoint (owner, id, lat, lon, name, descr, icon, expire, locked, sender, t, ch)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (owner, wid, lat / 1e7, lon / 1e7, str(w.get("name") or "")[:30], str(w.get("description") or "")[:100],
                        int(w.get("icon") or 0), expire, int(w.get("lockedTo") or 0), sender, now, packet.get("channel") or 0))
            wp_cleanup(owner, now)
    except sqlite3.Error:
        log.exception("не вдалося записати точку на карті")


def wp_json(r, me_id, i):
    wid, lat, lon, name, descr, icon, expire, locked, sender, t, ch = r
    return {"id": wid, "lat": lat, "lon": lon, "name": name, "description": descr,
            "icon": chr(icon) if 0x20 < icon <= 0x10FFFF and not 0xD800 <= icon < 0xE000 else chr(WP_ICON),
            "expire": None if expire >= WP_NEVER else expire, "locked": bool(locked), "sender": sender,
            "sender_name": node_name(i, sender) if i and sender else sender, "mine": sender == me_id, "t": t, "ch": ch}


@app.get("/api/waypoints")
def get_waypoints():
    owner, now, i = cur_id(), int(time.time()), cur_iface()
    me = ((i.getMyNodeInfo() or {}).get("user") or {}).get("id") if i else None
    with db_lock, db:
        wp_cleanup(owner, now)
        rows = db.execute("SELECT id, lat, lon, name, descr, icon, expire, locked, sender, t, ch FROM waypoint WHERE owner = ?"
                          " ORDER BY t DESC", (owner,)).fetchall()
    return jsonify([wp_json(r, me, i) for r in rows])


@app.post("/api/waypoints")
def post_waypoints():
    """action: send {id?, name, description, icon, lat, lon, ttl (с; 0 — без строку), lock, ch} | delete {id}."""
    d = body()
    i = need()
    owner, now = owner_of(i), int(time.time())
    me = i.getMyNodeInfo() or {}
    me_num, me_id = me.get("num"), (me.get("user") or {}).get("id")
    action, wid = d.get("action"), d.get("id")
    if action not in ("send", "delete"):
        return err("action: send | delete")
    if wid is not None and (type(wid) is not int or not 0 < wid < 2**32):
        return err("id: число")
    with db_lock:
        row = db.execute("SELECT locked FROM waypoint WHERE owner = ? AND id = ?", (owner, wid)).fetchone() if wid else None
    if row and row[0] and row[0] != me_num:
        return err("Точка заблокована за іншим вузлом: змінити чи видалити її може лише він", 403)
    w = mesh_pb2.Waypoint()
    if action == "delete":
        if not wid:
            return err("id: число")
        w.id, w.expire = wid, 0
        i.sendData(w, "^all", portNum=portnums_pb2.PortNum.WAYPOINT_APP, wantAck=True, channelIndex=int(d.get("ch", 0)) % 8)
        with db_lock, db:
            db.execute("DELETE FROM waypoint WHERE owner = ? AND id = ?", (owner, wid))
        return jsonify(ok=True)
    try:
        name, descr = str(d.get("name", "")).strip(), str(d.get("description", "")).strip()
        lat, lon, ttl, ch = float(d["lat"]), float(d["lon"]), int(d.get("ttl", 0)), int(d.get("ch", 0))
        icon = ord(str(d.get("icon") or chr(WP_ICON))[0])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or not 0 <= ch <= 7 or not (ttl == 0 or 60 <= ttl <= 365 * 86400):
            raise ValueError
        if not name or len(name.encode()) > 30 or len(descr.encode()) > 100:
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError, UnicodeEncodeError):
        return err("Точка: назва до 30 байт, опис до 100, координати, строк 0 (без) або від 60 с, канал 0..7")
    wid = wid or secrets.randbelow(10**9 - 1) + 1  # як у бібліотеки: id точки, а не пакета
    expire = WP_NEVER if ttl == 0 else now + ttl
    w.id, w.latitude_i, w.longitude_i, w.expire, w.name, w.description, w.icon = wid, int(lat * 1e7), int(lon * 1e7), expire, name, descr, icon
    if d.get("lock") is True and me_num:
        w.locked_to = me_num
    i.sendData(w, "^all", portNum=portnums_pb2.PortNum.WAYPOINT_APP, wantAck=True, channelIndex=ch)
    with db_lock, db:
        db.execute("INSERT OR REPLACE INTO waypoint (owner, id, lat, lon, name, descr, icon, expire, locked, sender, t, ch)"
                   " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (owner, wid, int(lat * 1e7) / 1e7, int(lon * 1e7) / 1e7, name, descr, icon, expire,
                                                         me_num if d.get("lock") is True and me_num else 0, me_id, now, ch))
        wp_cleanup(owner, now)
    return jsonify(id=wid)


# ---- адміністрування вузлів (свого й віддалених по радіо) ----
# Віддалені операції тривають десятки секунд, тому вони виконуються в потоці-завданні: браузер запускає
# його (/api/admin/job) і опитує стан. Одночасно на підключення — одне завдання (спільний ефір і стан підтверджень).
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
NODE_RE = re.compile(r"^(local|![0-9a-f]{8})$")
JOB_OPS = {"read", "write", "channels", "write_channel", "owner", "set_owner", "metadata", "fixed_position", "action",
           "request_position", "send_position", "canned", "set_canned", "share_url"}


@app.get("/api/admin/schema")
def admin_schema():
    s = admin.sections()
    return unchanged_304(jsonify(sections=s, schemas={n: admin.schema(n) for n in [*s["config"], *s["module"]]}))


def _run_job(job, c, op, args):
    try:
        i = c.iface
        if i is None:
            raise admin.AdminError("Вузол панелі не на зв'язку")
        job["result"] = admin.run(i, op, args, lambda t: job.update(progress=t))
        job["state"] = "done"
    except admin.AdminError as e:
        job.update(state="error", error=str(e))
    except (Exception, SystemExit) as e:  # noqa: BLE001 — бібліотека викликає sys.exit у деяких збоях
        log.exception("адмін-операція %s не вдалася", op)
        job.update(state="error", error=f"Збій операції: {str(e)[:160] or type(e).__name__}")
    finally:
        job["finished"] = time.time()
        c.admin_lock.release()


@app.post("/api/admin/job")
def admin_job():
    d = body()
    op, target = d.get("op"), d.get("target", "local")
    if op not in JOB_OPS or not isinstance(target, str) or not NODE_RE.match(target):
        return err("op: " + " | ".join(sorted(JOB_OPS)) + "; target: local або !xxxxxxxx")
    if op in ("read", "write") and d.get("section") not in [*admin.CONFIG_SECTIONS, *admin.MODULE_SECTIONS]:
        return err("Невідомий розділ")
    if op == "write" and d.get("section") in admin.READONLY:
        return err("Цей розділ лише для читання")
    if op == "action":
        name = d.get("name")
        if name not in admin.ACTIONS:
            return err("Невідома дія")
        if name in admin.CONFIRM and d.get("confirm") is not True:
            return err("Для цієї дії потрібне підтвердження (confirm: true)")
    if op in ("request_position", "send_position") and not (d.get("node") == "^all" and op == "send_position"
                                                           or isinstance(d.get("node"), str) and re.fullmatch(r"![0-9a-f]{8}", d["node"])):
        return err("node: !xxxxxxxx" + (" або ^all" if op == "send_position" else ""))
    need()  # 503, якщо вузол панелі не на зв'язку
    c = cur()
    if not c.admin_lock.acquire(blocking=False):
        return err("На цьому підключенні вже виконується інша адмін-операція — дочекайтеся її завершення", 409)
    now = time.time()
    job = {"id": secrets.token_hex(8), "op": op, "target": target, "conn": c.id, "state": "running", "progress": None,
           "started": now, "finished": None, "result": None, "error": None}
    with jobs_lock:
        for k in [k for k, v in jobs.items() if v["finished"] and now - v["finished"] > 3600]:
            del jobs[k]
        while len(jobs) >= 50:
            jobs.pop(next(iter(jobs)))
        jobs[job["id"]] = job
    args = {k: v for k, v in d.items() if k not in ("op",)}
    args["target"] = target
    threading.Thread(target=_run_job, args=(job, c, op, args), daemon=True).start()
    return jsonify(job=job["id"])


@app.get("/api/admin/job")
def admin_job_status():
    job = jobs.get(request.args.get("id", ""))
    if job is None:
        return err("Завдання не знайдено", 404)
    return jsonify({k: job[k] for k in ("id", "op", "target", "conn", "state", "progress", "started", "finished", "result", "error")})


load_conns()  # реєстр має бути непорожнім уже при імпорті (тести, обробники)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not auth_on():
        log.warning("вхід без пароля — інтерфейс відкритий для всієї мережі")
    init_db(DB_PATH)
    pub.subscribe(on_text, "meshtastic.receive.text")
    pub.subscribe(on_packet, "meshtastic.receive")
    pub.subscribe(on_lost, "meshtastic.connection.lost")
    run_threads = True
    load_conns()  # старт потоків підключення для кожного вузла зі списку
    for _c in list(conns.values()):
        threading.Thread(target=conn_loop, args=(_c,), daemon=True).start()
    threading.Thread(target=sampler, daemon=True).start()
    threading.Thread(target=autotrace, daemon=True).start()
    threading.Thread(target=scan_recover, daemon=True).start()
    # shortcut: HTTP без TLS; для доступу поза домашньою мережею — зворотний проксі з TLS (README, розділ 6)
    serve(app, host=os.environ.get("MESHPI_BIND", "0.0.0.0"), port=int(os.environ.get("MESHPI_PORT", "8080")),
          threads=6, max_request_body_size=2_000_000)  # 6: частина запитів чекає journalctl; 2 МБ — копія
