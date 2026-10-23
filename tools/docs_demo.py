"""Демо-сервер для скриншотів у README: вигадані вузли, повідомлення й показники, без радіо й без жодних
справжніх даних. Запуск:  python tools/docs_demo.py webui [порт] [--login]
--login вмикає пароль (demo / demo), щоб зняти сторінку входу."""
import json
import math
import os
import random
import sys
import tempfile
import threading
import time
import types

webui, port = sys.argv[1], int(next((a for a in sys.argv[2:] if a.isdigit()), 8099))
tmp = tempfile.mkdtemp()
os.environ["MESHPI_DB"] = os.path.join(tmp, "messages.db")
if "--login" in sys.argv:
    os.environ.update(MESHPI_USER="demo", MESHPI_PASSWORD="demo", MESHPI_VIEWER_PASSWORD="demo-viewer-1")
else:
    os.environ.pop("MESHPI_PASSWORD", None)
sys.path.insert(0, webui)
import app as m  # noqa: E402
from flask import jsonify  # noqa: E402
import admin  # noqa: E402
from meshtastic.protobuf import admin_pb2, channel_pb2, localonly_pb2, mesh_pb2  # noqa: E402

random.seed(11)
m.init_db(m.DB_PATH)
now = int(time.time())
ME = 0x1000
LAT, LON = 49.2, 31.4  # вигадана точка


def pos(dkm, bearing):
    return {"latitude": LAT + dkm * math.cos(bearing) / 111.0,
            "longitude": LON + dkm * math.sin(bearing) / (111.0 * math.cos(math.radians(LAT)))}


def node(num, name, short, hops, dkm=None, brg=0.0, role=None, snr=None, mqtt=False, heard=60, env=None):
    n = {"num": num, "user": {"id": f"!{num:08x}", "longName": name, "shortName": short, "hwModel": "RAK4631"},
         "hopsAway": hops, "viaMqtt": mqtt, "lastHeard": now - heard, "snr": snr, "environmentMetrics": env or {}}
    if role:
        n["user"]["role"] = role
    if dkm is not None:
        n["position"] = pos(dkm, brg)
    return n


NODES = [
    node(ME, "Демо-база", "DEMO", 0, 0.0),
    node(0x2001, "Пагорб-1 (роутер)", "PG01", 0, 1.8, 0.6, "ROUTER", 9.5, env={"temperature": 17.4, "relativeHumidity": 54, "barometricPressure": 1011.2}),
    node(0x2002, "Дача Олега", "OLEG", 0, 0.9, 3.5, "CLIENT", 7.0, heard=300),
    node(0x2003, "Млин", "MLYN", 1, 3.1, 1.3, "CLIENT_MUTE", 4.5, heard=900),
    node(0x2004, "Вежа-репітер", "VEZH", 1, 5.4, 2.2, "REPEATER", 3.0, env={"temperature": 16.1, "barometricPressure": 1010.4}),
    node(0x2005, "Трекер Лісник", "LISN", 2, 6.0, 0.2, "TRACKER", -2.5, heard=1800),
    node(0x2006, "Метеостанція Поле", "METE", 2, 4.2, 4.4, "SENSOR", 1.0, env={"temperature": 18.2, "relativeHumidity": 49, "barometricPressure": 1012.0}),
    node(0x2007, "Хутір", "HUTR", 2, None, 0, "CLIENT", -5.0, heard=3600),
    node(0x2008, "Школа", "SHKL", 1, 2.6, 5.4, "CLIENT_BASE", 5.5),
    node(0x2009, "Ферма (пізній роутер)", "FERM", 3, 9.5, 2.8, "ROUTER_LATE", -7.0, heard=5400),
    node(0x200A, "Лис", "LYS", 3, 8.0, 5.9, "CLIENT_HIDDEN", -8.5, heard=7200),
    node(0x200B, "Маяк-ТАК", "TAK1", 3, None, 0, "TAK", -9.0, heard=9000),
    node(0x200C, "Знахідка", "FIND", 4, 12.0, 1.0, "LOST_AND_FOUND", -12.0, heard=14000),
    node(0x200D, "Гість з MQTT", "MQTT", 2, 7.0, 3.0, "CLIENT", None, mqtt=True, heard=4000),
    node(0x200E, "Сусід-клієнт", "SUSD", 1, 1.4, 4.9, "CLIENT", 6.5, heard=120),
]
import base64  # noqa: E402
for _n in NODES:   # вигадані публічні ключі: у вузлів 0x2007 і 0x2009 однакові (як клони), решта різні
    _seed = 0x2007 if _n["num"] == 0x2009 else _n["num"]
    _n["user"]["publicKey"] = base64.b64encode(bytes((_seed * (k + 3) + k) % 251 + 1 for k in range(32))).decode()
nodes = {n["num"]: n for n in NODES}
me = {"num": ME, "user": {"id": f"!{ME:08x}", "longName": "Демо-база", "shortName": "DEMO", "role": "CLIENT"},
      "deviceMetrics": {"batteryLevel": 87, "voltage": 4.05, "channelUtilization": 6.2, "airUtilTx": 1.1, "uptimeSeconds": 345600}}
nodes[ME] = {**nodes[ME], **me}


class DemoIface:
    """Підставний інтерфейс: «свій» вузол зі справжніми protobuf-налаштуваннями й віддалені, що відповідають на
    адмін-запити із затримкою, як по радіо. Нічого не передається в ефір і не змінює реальних налаштувань."""

    def __init__(self):
        LC, MC = localonly_pb2.LocalConfig, localonly_pb2.LocalModuleConfig
        self.nodesByNum, self.nodes = nodes, {n["user"]["id"]: n for n in NODES}
        self.cfg, self.mod = LC(), MC()
        self.cfg.lora.region, self.cfg.lora.modem_preset, self.cfg.lora.use_preset, self.cfg.lora.hop_limit = 2, 0, True, 3
        self.cfg.device.role = 0
        self.cfg.position.position_broadcast_secs = 900
        self.mod.telemetry.device_update_interval = 1800
        self.mod.mqtt.enabled = True
        chans = []
        for k in range(8):
            ch = channel_pb2.Channel(index=k, role=1 if k == 0 else 0)
            if k < 2:
                ch.settings.name, ch.settings.psk = ("LongFast" if k == 0 else "Дача"), (b"\x01" if k == 0 else bytes(range(1, 33)))
                ch.settings.uplink_enabled = k == 1
            chans.append(ch)
        self.remote = {"cfg": LC(), "mod": MC(), "chans": chans, "keys": {}}
        self.remote["cfg"].lora.region, self.remote["cfg"].device.role = 2, 2
        self.localNode = types.SimpleNamespace(nodeNum=ME, localConfig=self.cfg, moduleConfig=self.mod, channels=chans[:],
                                               writeConfig=lambda s: None, writeChannel=lambda k: None,
                                               setOwner=lambda a, b: None, setFixedPosition=lambda *a: None,
                                               getURL=lambda includeAll=True: 'https://meshtastic.org/e/#CgcSAQE6AggBEg0IARAEOAFAA0gBUBo',
                                               _getAdminChannelIndex=lambda: 0)
        self._keys = {}

    def getMyNodeInfo(self):
        return me

    def _getOrCreateByNum(self, num):
        return self._keys.setdefault(num, {})

    def sendData(self, p, num=None, portNum=None, wantAck=False, wantResponse=False, onResponse=None, onResponseAckPermitted=False,
                 channelIndex=0, pkiEncrypted=False, destinationId=None, replyId=None, hopLimit=None):
        num = destinationId if num is None else num
        if isinstance(p, bytes):  # текст: «доставлено» через 1,5 с, як підтвердження від адресата
            self._pid = getattr(self, "_pid", 5000) + 1
            if onResponse:
                threading.Timer(1.5, onResponse, args=({"fromId": num, "decoded": {"portnum": "ROUTING_APP", "requestId": self._pid,
                                                                                     "routing": {"errorReason": "NONE"}}},)).start()
            return types.SimpleNamespace(id=self._pid)
        if isinstance(p, mesh_pb2.Waypoint):  # точка на карті: підтвердження не потрібне
            return None
        if isinstance(p, mesh_pb2.Position):  # запит позиції вузла
            if onResponse:
                n = nodes.get(int(str(num).lstrip("!"), 16) if isinstance(num, str) else num, {})
                pos = n.get("position") or {}
                reply = {"decoded": {"portnum": "POSITION_APP", "position": {"latitudeI": int(pos.get("latitude", 0) * 1e7),
                                                                          "longitudeI": int(pos.get("longitude", 0) * 1e7), "altitude": 180}}}
                threading.Timer(1.2, onResponse, args=(reply,)).start()
            return None
        r, kind = admin_pb2.AdminMessage(), p.WhichOneof("payload_variant")
        pk = {"decoded": {"portnum": "ROUTING_APP", "routing": {"errorReason": "NONE"}}}
        if kind == "get_config_request":
            name = admin_pb2.AdminMessage.ConfigType.Name(p.get_config_request).replace("_CONFIG", "").lower()
            if name == "sessionkey":
                self._keys.setdefault(num, {})["adminSessionPassKey"] = b"demo"
            else:
                getattr(r.get_config_response, name).CopyFrom(getattr(self.remote["cfg"], name))
            pk = {"decoded": {"portnum": "ADMIN_APP", "admin": {"raw": r}}}
        elif kind == "get_module_config_request":
            name = {v: k for k, v in admin.MODULE_SECTIONS.items()}[admin_pb2.AdminMessage.ModuleConfigType.Name(p.get_module_config_request)]
            getattr(r.get_module_config_response, name).CopyFrom(getattr(self.remote["mod"], name))
            pk = {"decoded": {"portnum": "ADMIN_APP", "admin": {"raw": r}}}
        elif kind == "get_channel_request":
            r.get_channel_response.CopyFrom(self.remote["chans"][p.get_channel_request - 1])
            pk = {"decoded": {"portnum": "ADMIN_APP", "admin": {"raw": r}}}
        elif kind == "get_owner_request":
            r.get_owner_response.long_name, r.get_owner_response.short_name = "Віддалений вузол", "RMT"
            pk = {"decoded": {"portnum": "ADMIN_APP", "admin": {"raw": r}}}
        elif kind == "get_canned_message_module_messages_request":
            r.get_canned_message_module_messages_response = self.canned
            pk = {"decoded": {"portnum": "ADMIN_APP", "admin": {"raw": r}}}
        elif kind == "set_canned_message_module_messages":
            self.canned = p.set_canned_message_module_messages
        if onResponse:
            threading.Timer(1.2, onResponse, args=(pk,)).start()

    canned = "Так|Ні|Іду до вас|Потрібна допомога"

    def sendPosition(self, *a, **k):
        pass


ID = lambda n: f"!{n:08x}"  # noqa: E731
m.use_iface(DemoIface())

# повідомлення
msgs = [(-7200, 0x2001, "Пагорб-1 (роутер)", "^all", "Доброго ранку! Хто на зв'язку?"),
        (-7100, 0x2002, "Дача Олега", "^all", "На зв'язку, чую добре"),
        (-6900, ME, "Ви", "^all", "Перевірка зв'язку з бази: приймання 9 дБ"),
        (-5400, 0x2004, "Вежа-репітер", "^all", "ping"),
        (-5390, ME, "Ви", "^all", "Чую PING, відповідаю PONG, почув за 1 стрибок"),
        (-3600, 0x2008, "Школа", ID(ME), "Привіт, чи доступна база сьогодні ввечері?"),
        (-3500, ME, "Ви", ID(0x2008), "Так, буду до 20:00"),
        (-1800, 0x2006, "Метеостанція Поле", "^all", "Тиск 1012 гПа, температура 18 °C"),
        (-600, 0x200E, "Сусід-клієнт", "^all", "Хтось чує мене з хутора?")]
with m.db_lock, m.db:
    for k, (dt, num, name, dest, text) in enumerate(msgs):
        m.db.execute(f"INSERT INTO msg ({m.MSG_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (now + dt, None if num == ME else ID(num), name, dest, 0, text, None if num == ME else 6.0,
                      int(num == ME), 1000 + k, None, 0))
    # графіки за добу
    for t in range(now - 86400, now, 300):
        x = (t - now) / 86400 * 2 * math.pi
        m.db.execute("INSERT OR REPLACE INTO metric (t, chan, air, nodes) VALUES (?,?,?,?)",
                     (t, 5 + 3 * math.sin(x) + random.random(), 0.8 + 0.5 * math.sin(x + 1) + random.random() * 0.2,
                      13 + int(2 * math.sin(x * 3))))
    # довкілля
    for n in NODES:
        for k, v in {"temperature": 17, "humidity": 52, "pressure": 1011}.items():
            if n["environmentMetrics"]:
                for t in range(now - 86400, now, 1800):
                    x = (t - now) / 86400 * 2 * math.pi
                    base = n["environmentMetrics"].get({"temperature": "temperature", "humidity": "relativeHumidity", "pressure": "barometricPressure"}[k])
                    if base is not None:
                        m.db.execute("INSERT INTO env (t, node, hops, k, v) VALUES (?,?,?,?,?)",
                                     (t, n["user"]["id"], n["hopsAway"], k, base + {"temperature": 3, "humidity": 8, "pressure": 2}[k] * math.sin(x)))

# телеметрія вузлів (батарея, напруга, канал, INA219 на роутері) і точки на карті
with m.db_lock, m.db:
    for n in NODES[1:8]:
        for t in range(now - 86400, now, 1800):
            x = (t - now) / 86400 * 2 * math.pi
            nid = n["user"]["id"]
            for k, v in (("bat", 62 + 30 * math.sin(x + n["num"] % 7)), ("volt", 3.9 + 0.25 * math.sin(x + n["num"] % 7)),
                         ("chan", 6 + 4 * math.sin(2 * x + n["num"] % 5) + random.random()), ("air", 0.8 + 0.4 * math.sin(x))):
                m.db.execute("INSERT INTO tele (owner, node, t, k, v) VALUES ('main', ?, ?, ?, ?)", (nid, t, k, v))
            if n["num"] == 0x2001:
                m.db.execute("INSERT INTO tele (owner, node, t, k, v) VALUES ('main', ?, ?, 'p1v', ?)", (nid, t, 5.0 + 0.1 * math.sin(3 * x)))
                m.db.execute("INSERT INTO tele (owner, node, t, k, v) VALUES ('main', ?, ?, 'p1c', ?)", (nid, t, 380 + 60 * math.sin(2 * x)))
    for wid, (name, descr, icon, dlat, dlon, ttl, mine) in {9001: ("Місце збору", "Біля млина, о 18:00", "🚩", 0.012, 0.02, 7200, True),
                                                            9002: ("Колодязь", "Чиста вода", "💧", -0.02, -0.015, None, False)}.items():
        m.db.execute("INSERT INTO waypoint (owner, id, lat, lon, name, descr, icon, expire, locked, sender, t, ch)"
                     " VALUES ('main', ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 0)",
                     (wid, LAT + dlat, LON + dlon, name, descr, ord(icon), now + ttl if ttl else 2**31 - 1,
                      f"!{ME:08x}" if mine else "!00002001", now - 600))

# зв'язки для карти
LINKS = [(0x2001, ME, "up", "relay", 120), (0x2002, ME, "up", "direct", 60), (0x200E, ME, "up", "direct", 35),
         (0x2008, 0x2001, "up", "direct", 28), (0x2003, 0x2001, "up", "direct", 22), (0x2004, 0x2001, "up", "direct", 18),
         (0x2006, 0x2004, "up", "direct", 14), (0x2005, 0x2004, "up", "via", 8), (0x2007, 0x2002, "up", "via", 5),
         (0x2009, 0x2004, "up", "via", 6), (0x200A, 0x2003, "up", "via", 4), (0x200B, 0x2003, "up", "via", 3),
         (0x200C, 0x2004, "up", "via", 2), (0x200D, 0x2008, "up", "direct", 9),
         (ME, 0x2001, "down", "direct", 20), (ME, 0x2002, "down", "direct", 9)]
for a, b, flow, kind, c in LINKS:
    for _ in range(c):
        m.add_link(ID(a), ID(b), flow, kind, 5.0 + (a % 7) / 2)
hop = lambda n, s=None: {"id": ID(n), "name": "x", "snr": s}  # noqa: E731
m.record_trace({"towards": [hop(ME), hop(0x2001, 8.5), hop(0x2004, 6.0), hop(0x2006, 3.5)],
                "back": [hop(0x2006), hop(0x2003, 4.0), hop(0x2001, 7.0), hop(ME, 9.0)]})
m.flush_links(True)


# «системні» кінцеві точки (на Windows без /proc і systemd): вигадані значення
def stub(name, fn):
    m.app.view_functions[name] = fn


stub("system", lambda: jsonify(temp=47.5, load=0.31, cpu=7.0, mem_free=780, mem_total=1900, disk_free=21000, disk_total=29000))
stub("health", lambda: jsonify(spi=True, service="active", errors=[], connected=True))
stub("power", lambda: jsonify(setting=14, logged=14, cap=22, radio="lora-demo.yaml", effective=14, patched=False,
                              running=False, held=False, installed="2.7.0", candidate="2.7.0", log=""))
# журнал подій для скриншота (вигадані вузли)
for ago_s, kind, text in ((5400, "new", "Новий вузол Ліс-3 (Лісовий вузол 3)"), (3300, "fav_off", "★ Гора не на зв'язку: не чути 35 хв"),
                          (1500, "fav_on", "★ Гора знову на зв'язку"), (420, "new", "Новий вузол Дача (Дача-Ретранслятор)")):
    m.add_event("main", kind, "!00000001", text, now - ago_s)

# ---- дані для скриншотів нових розділів (усе вигадане) ----
# здоров'я мережі: лічильники LocalStats свого вузла кожні 15 хв, трохи забагато дублікатів
with m.db_lock, m.db:
    cnt = {"ls_rx": 0, "ls_dup": 0, "ls_bad": 0, "ls_tx": 0, "ls_rel": 0, "ls_can": 0, "ls_drop": 0}
    for k, t in enumerate(range(now - 86400, now, 900)):
        bump = 1.9 if 40 < k % 96 < 60 else 1.0     # ввечері дублікатів більше
        for key, per in (("ls_rx", 52), ("ls_dup", 9 * bump), ("ls_bad", 4), ("ls_tx", 7), ("ls_rel", 14), ("ls_can", 9), ("ls_drop", 0)):
            cnt[key] += int(per + (random.random() * 3 if per else 0))
            m.db.execute("INSERT INTO tele (owner, node, t, k, v) VALUES ('main', ?, ?, ?, ?)", (ID(ME), t, key, cnt[key]))
        m.db.execute("INSERT INTO tele (owner, node, t, k, v) VALUES ('main', ?, ?, 'ls_noise', ?)", (ID(ME), t, -113 + 2 * math.sin(k / 6) + random.random()))
    # вимір дальності: «Лісник» іде від бази, 80 пакетів «seq N» за дві години, частину втрачено
    brg, seq, t0 = 0.2, 0, now - 7200
    for k in range(80):
        d = 0.15 + 6.3 * k / 80
        seq += 1 if random.random() > 0.12 else 2
        snr = 9 - 3.3 * d + random.random() * 3
        p = pos(d, brg)
        m.db.execute("INSERT INTO rangetest (owner, t, node, seq, snr, rssi, hops, lat, lon, dist) VALUES ('main', ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                     (t0 + k * 90, ID(0x2005), seq, snr, snr - 105, p["latitude"], p["longitude"], d))
    # журнал дій
    for ago_s, role, user, ip, act in ((7000, "admin", "demo", "10.10.92.5", "вхід"), (6900, "admin", "demo", "10.10.92.5", "POST /api/send → 200"),
                                       (5200, "", "admin", "10.10.92.77", "невдалий вхід"), (4100, "viewer", "viewer", "10.10.92.41", "вхід"),
                                       (4000, "viewer", "viewer", "10.10.92.41", "POST /api/send → 403"),
                                       (3000, "admin", "demo", "10.10.92.5", "POST /api/notify → 200"), (1800, "система", "", "", "автоматика → !00002008: [авто] Meshpi: почув вас за 1 стрибків, SNR 5 (відповідь)"),
                                       (600, "admin", "demo", "10.10.92.5", "POST /api/viewer → 200")):
        m.db.execute("INSERT INTO audit (t, role, user, ip, action) VALUES (?,?,?,?,?)", (now - ago_s, role, user, ip, act))
# журнал пакетів
for k in range(60):
    n = random.choice(NODES[1:])
    ptype = random.choice(["TEXT_MESSAGE_APP", "TELEMETRY_APP", "POSITION_APP", "NODEINFO_APP", "TELEMETRY_APP", "ЗАШИФРОВАНО", "TRACEROUTE_APP"])
    m.pkt_log.setdefault("main", __import__("collections").deque(maxlen=m.PKT_LOG)).append({
        "t": now - k * 37, "from": n["user"]["id"], "to": "^all" if k % 3 else ID(ME), "port": ptype, "snr": round(n["snr"] + random.uniform(-2, 2), 1) if n["snr"] is not None else None,
        "rssi": -90 - random.randint(0, 30), "hops": n["hopsAway"], "relay": f"{n['num'] & 255:02x}" if n["hopsAway"] else None, "mqtt": n["viaMqtt"], "ch": 0, "id": 7000 + k})
# сповіщення, автоматика й сонячний вузол
m.notify_cfg["telegram"].update(on=True, token="123456789:" + "A" * 35, chat="-1001234567890")
m.notify_cfg["kinds"]["new"] = True
m.auto_cfg["reply"].update(on=True, scope="dm")
m.auto_cfg["reply"]["rules"] = [{"word": "info", "text": "Meshpi: почув вас за {hops} стрибків, SNR {snr}. Вузлів за годину: {nodes}."},
                                {"word": "погода", "text": "База: {temp} °C, {hum}%, {press} гПа, заряд {bat}"}]
m.auto_cfg["announce"].update(on=True, items=[{"id": 1, "text": "CQ від Демо-бази: мережа працює, відповідаємо на «info»", "every": 360, "ch": 0, "quiet_from": 23, "quiet_to": 6}])
m.SOLAR_FILE = os.path.join(tmp, "solar.json")
open(m.SOLAR_FILE, "w").write(json.dumps({"main": {"on": True, "lat": LAT, "lon": LON, "dec": 35, "az": 0, "kwp": 0.1, "cap_wh": 24.0, "load_w": 0.45, "eff": 0.75}}))
me["deviceMetrics"]["batteryLevel"] = 58


def demo_fetch(url, **kw):
    """Підставні відповіді open-meteo.com і forecast.solar: у демо нічого не йде в інтернет."""
    if "elevation" in url:
        n = url.split("latitude=")[1].split("&")[0].count(",") + 1
        return {"elevation": [185 + 22 * math.exp(-((k - n * 0.55) / (n * 0.09)) ** 2) + 6 * math.sin(k / 9) for k in range(n)]}
    off = time.localtime(now).tm_gmtoff
    iso = lambda t, o=off: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t + o)) + f"{'+' if o >= 0 else '-'}{abs(o) // 3600:02d}:{abs(o) % 3600 // 60:02d}"  # noqa: E731
    watts, days = {}, {}
    for day in (0, 1):
        t0 = (now // 86400 + day) * 86400 - off          # північ за місцевим часом
        for h in range(7, 18):
            w = max(0, 78 * math.sin(math.pi * (h - 7) / 10)) * (1 if day == 0 else 0.45)   # завтра похмуро
            watts[time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t0 + h * 3600 + off))] = round(w)
            days[time.strftime("%Y-%m-%d", time.gmtime(t0 + off))] = days.get(time.strftime("%Y-%m-%d", time.gmtime(t0 + off)), 0) + round(w)
    return {"result": {"watts": watts, "watt_hours_day": days}, "message": {"info": {"time": iso(now), "time_utc": iso(now, 0)}, "ratelimit": {"period": 3600, "limit": 12, "remaining": 9}}}


m.fetch_json = demo_fetch
# роль адміністратора з увімкненим входом і роллю «лише перегляд»: так виглядає розділ «Доступ» на скриншоті
stub("api_whoami", lambda: jsonify(role="admin", auth=True, viewer=True, viewer_user="viewer"))


@m.app.after_request
def shot_helper(resp):
    """Лише в демо: ?shot=<id секції>[&click=<id кнопок>][&set=<id:значення,…>] залишає на сторінці лише цю секцію вкладки після
    завантаження (для знімків headless Chrome, див. docs/WEB-PANEL.md): спершу set, потім click."""
    import re
    from flask import request as rq
    sid = rq.args.get("shot")
    if not (sid and resp.mimetype == "text/html" and re.fullmatch(r"\w+", sid)):
        return resp
    click = ",".join(x for x in rq.args.get("click", "").split(",") if re.fullmatch(r"\w+", x))
    sets = [x.split(":", 1) for x in rq.args.get("set", "").split(",") if re.fullmatch(r"\w+:[\w!.-]+", x)]
    js = ("<script>addEventListener('load',()=>setTimeout(()=>{for(const [i,v] of %s){const e=document.getElementById(i); if(e){e.value=v;}}"
          "for(const b of %r.split(',')) if (b) document.getElementById(b)?.click();"
          "setTimeout(()=>{const sec=document.getElementById(%r).closest('section'), pane=sec.closest('.tabpane');"
          "for(const p of pane.querySelectorAll(':scope > section.panel, :scope > .strip')) if(p!==sec) p.style.display='none';}, %d);}, 2500));</script>"
          % (json.dumps(sets), click, sid, 5000 if click else 800))
    resp.set_data(resp.get_data(as_text=True).replace("</body>", js + "</body>"))
    resp.headers.pop("Content-Length", None)
    return resp


m.app.run("127.0.0.1", port)
