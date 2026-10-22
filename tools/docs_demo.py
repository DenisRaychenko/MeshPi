"""Демо-сервер для скриншотів у README: вигадані вузли, повідомлення й показники, без радіо й без жодних
справжніх даних. Запуск:  python tools/docs_demo.py webui [порт] [--login]
--login вмикає пароль (demo / demo), щоб зняти сторінку входу."""
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
    os.environ.update(MESHPI_USER="demo", MESHPI_PASSWORD="demo")
else:
    os.environ.pop("MESHPI_PASSWORD", None)
sys.path.insert(0, webui)
import app as m  # noqa: E402
from flask import jsonify  # noqa: E402
import admin  # noqa: E402
from meshtastic.protobuf import admin_pb2, channel_pb2, localonly_pb2  # noqa: E402

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
        self.mod.mqtt.enabled = False
        chans = []
        for k in range(8):
            ch = channel_pb2.Channel(index=k, role=1 if k == 0 else 0)
            if k < 2:
                ch.settings.name, ch.settings.psk = ("LongFast" if k == 0 else "Дача"), bytes([1 + k]) * 16
            chans.append(ch)
        self.remote = {"cfg": LC(), "mod": MC(), "chans": chans, "keys": {}}
        self.remote["cfg"].lora.region, self.remote["cfg"].device.role = 2, 2
        self.localNode = types.SimpleNamespace(nodeNum=ME, localConfig=self.cfg, moduleConfig=self.mod, channels=chans[:],
                                               writeConfig=lambda s: None, writeChannel=lambda k: None,
                                               setOwner=lambda a, b: None, setFixedPosition=lambda *a: None,
                                               _getAdminChannelIndex=lambda: 0)
        self._keys = {}

    def getMyNodeInfo(self):
        return me

    def _getOrCreateByNum(self, num):
        return self._keys.setdefault(num, {})

    def sendData(self, p, num, portNum=None, wantAck=False, wantResponse=False, onResponse=None, onResponseAckPermitted=False,
                 channelIndex=0, pkiEncrypted=False):
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
        if onResponse:
            threading.Timer(1.2, onResponse, args=(pk,)).start()


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
m.app.run("127.0.0.1", port)
