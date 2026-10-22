"""Адміністрування вузлів Meshtastic з веб-панелі: свого вузла (через TCP API) і віддалених по радіо через свій
вузол (адмін-пакети з PKC-шифруванням; на віддаленому ключ цього вузла має бути в security.admin_key).

Налаштування читаються й пишуться як у застосунку Meshtastic: розділи конфігурації (device, lora, position…), розділи
модулів (mqtt, serial, telemetry…), канали, назва вузла, фіксована позиція, дії (перезавантаження, вимкнення, скидання).
Форма в браузері будується з описів полів protobuf (schema), тож нові поля прошивки з'являються самі."""
import base64
import threading
import time

from google.protobuf import json_format
from google.protobuf.descriptor import FieldDescriptor as FD
from meshtastic.protobuf import admin_pb2, channel_pb2, localonly_pb2, mesh_pb2, portnums_pb2
from meshtastic.util import to_node_num

# розділи основної конфігурації (LocalConfig) і модулів (LocalModuleConfig → тип запиту в AdminMessage)
CONFIG_SECTIONS = ["device", "position", "power", "network", "display", "lora", "bluetooth", "security"]
MODULE_SECTIONS = {
    "mqtt": "MQTT_CONFIG", "serial": "SERIAL_CONFIG", "external_notification": "EXTNOTIF_CONFIG",
    "store_forward": "STOREFORWARD_CONFIG", "range_test": "RANGETEST_CONFIG", "telemetry": "TELEMETRY_CONFIG",
    "canned_message": "CANNEDMSG_CONFIG", "audio": "AUDIO_CONFIG", "remote_hardware": "REMOTEHARDWARE_CONFIG",
    "neighbor_info": "NEIGHBORINFO_CONFIG", "ambient_lighting": "AMBIENTLIGHTING_CONFIG",
    "detection_sensor": "DETECTIONSENSOR_CONFIG", "paxcounter": "PAXCOUNTER_CONFIG",
    "statusmessage": "STATUSMESSAGE_CONFIG", "traffic_management": "TRAFFICMANAGEMENT_CONFIG", "tak": "TAK_CONFIG",
}
# розділ security лише для читання: у ньому приватний ключ вузла, а запис із порожнім ключем міг би стерти особистість вузла
READONLY = {"security"}
HIDDEN = {"security": {"private_key"}}  # ці поля ніколи не віддаємо в браузер
DESTRUCTIVE = {"factory_reset_config", "factory_reset_device", "nodedb_reset", "shutdown_seconds", "remove_by_nodenum"}

ERRORS = {  # errorReason маршрутизації → пояснення
    "NO_ROUTE": "немає маршруту до вузла", "GOT_NAK": "вузол відхилив запит", "TIMEOUT": "вузол не відповів",
    "NO_INTERFACE": "немає інтерфейсу", "MAX_RETRANSMIT": "вузол не відповів після повторів",
    "NO_CHANNEL": "немає каналу для запиту", "TOO_LARGE": "пакет задовгий", "NO_RESPONSE": "вузол не відповів",
    "DUTY_CYCLE_LIMIT": "перевищено ліміт ефіру (duty cycle): спробуйте пізніше",
    "BAD_REQUEST": "некоректний запит", "NOT_AUTHORIZED": "вузол не дозволяє керування",
    "PKI_FAILED": "помилка PKC-шифрування", "PKI_UNKNOWN_PUBKEY": "невідомий відкритий ключ вузла: дочекайтеся, поки він передасть NodeInfo",
    "ADMIN_BAD_SESSION_KEY": "прострочений ключ сесії: повторіть операцію",
    "ADMIN_PUBLIC_KEY_UNAUTHORIZED": "ключ цього вузла не внесено в security.admin_key віддаленого вузла",
    "RATE_LIMIT_EXCEEDED": "забагато запитів до вузла",
    "PKI_SEND_FAIL_PUBLIC_KEY": "вузол ще не відомий із відкритим ключем: дочекайтеся його NodeInfo",
}


class AdminError(Exception):
    """Помилка адмін-операції: текст готовий для показу користувачу."""


# ---- опис полів для форми ----
_KIND = {FD.TYPE_BOOL: "bool", FD.TYPE_STRING: "string", FD.TYPE_BYTES: "bytes", FD.TYPE_ENUM: "enum",
         FD.TYPE_MESSAGE: "message", FD.TYPE_FLOAT: "float", FD.TYPE_DOUBLE: "float"}
_INT_RANGE = {FD.TYPE_UINT32: (0, 2**32 - 1), FD.TYPE_INT32: (-2**31, 2**31 - 1), FD.TYPE_FIXED32: (0, 2**32 - 1),
              FD.TYPE_SFIXED32: (-2**31, 2**31 - 1), FD.TYPE_SINT32: (-2**31, 2**31 - 1),
              FD.TYPE_UINT64: (0, 2**64 - 1), FD.TYPE_INT64: (-2**63, 2**63 - 1), FD.TYPE_FIXED64: (0, 2**64 - 1),
              FD.TYPE_SFIXED64: (-2**63, 2**63 - 1), FD.TYPE_SINT64: (-2**63, 2**63 - 1)}


def _descriptor(section):
    if section in CONFIG_SECTIONS:
        return localonly_pb2.LocalConfig.DESCRIPTOR.fields_by_name[section].message_type
    if section in MODULE_SECTIONS:
        return localonly_pb2.LocalModuleConfig.DESCRIPTOR.fields_by_name[section].message_type
    raise AdminError(f"Невідомий розділ «{section}»")


def _repeated(fd):
    r = getattr(fd, "is_repeated", None)  # нові protobuf (label прибрано), старі — через label
    return r if r is not None else fd.label == FD.LABEL_REPEATED


def _field(fd):
    out = {"name": fd.name, "repeated": bool(_repeated(fd))}
    if fd.type in _INT_RANGE:
        out["type"], (out["min"], out["max"]) = "int", _INT_RANGE[fd.type]
    else:
        out["type"] = _KIND.get(fd.type, "string")
    if fd.type == FD.TYPE_ENUM:
        out["options"] = [v.name for v in fd.enum_type.values]
    if fd.type == FD.TYPE_MESSAGE:
        out["fields"] = [_field(f) for f in fd.message_type.fields]
    return out


def schema(section):
    """Поля розділу для побудови форми (приховані поля — без приватних ключів)."""
    d = _descriptor(section)
    return [_field(f) for f in d.fields if f.name not in HIDDEN.get(section, ())]


def sections():
    return {"config": CONFIG_SECTIONS, "module": list(MODULE_SECTIONS), "readonly": sorted(READONLY)}


def to_values(msg, section):
    try:
        d = json_format.MessageToDict(msg, preserving_proto_field_name=True, always_print_fields_with_no_presence=True)
    except TypeError:  # старіша бібліотека protobuf
        d = json_format.MessageToDict(msg, preserving_proto_field_name=True, including_default_value_fields=True)
    for k in HIDDEN.get(section, ()):
        d.pop(k, None)
    return d


def _parse(cur, section, values):
    """Нове повідомлення з values; приховані поля лишаються як були (їх браузер не бачить)."""
    if not isinstance(values, dict):
        raise AdminError("Очікувався об'єкт зі значеннями полів")
    new = type(cur)()
    try:
        json_format.ParseDict(values, new)
    except (json_format.ParseError, ValueError, TypeError) as e:
        raise AdminError(f"Некоректне значення: {str(e)[:160]}") from e
    for k in HIDDEN.get(section, ()):
        if cur.HasField(k) if cur.DESCRIPTOR.fields_by_name[k].has_presence else getattr(cur, k):
            setattr(new, k, getattr(cur, k))
    return new


# ---- відправка адмін-пакетів ----
def _num(i, target):
    return i.localNode.nodeNum if target == "local" else to_node_num(target)


def is_local(i, target):
    return target == "local" or _num(i, target) == i.localNode.nodeNum


def send_admin(i, num, p, cb, ack_ok=False):
    """Адмін-пакет вузлу num (PKC, як у бібліотеки: Node._sendAdmin), але з обробником, що чекає й підтвердження."""
    entry = i._getOrCreateByNum(num)
    if entry.get("adminSessionPassKey"):
        p.session_passkey = entry["adminSessionPassKey"]
    return i.sendData(p, num, portNum=portnums_pb2.PortNum.ADMIN_APP, wantAck=True, wantResponse=True, onResponse=cb,
                      onResponseAckPermitted=ack_ok, channelIndex=i.localNode._getAdminChannelIndex(), pkiEncrypted=True)


def call(i, target, p, timeout, ack_ok=False, retry_session=True):
    """Надіслати й дочекатися відповіді (або підтвердження, якщо ack_ok). Повертає пакет-словник."""
    num = _num(i, target)
    ev, box = threading.Event(), {}

    def cb(pk):
        box["p"] = pk
        ev.set()

    send_admin(i, num, p, cb, ack_ok)
    if not ev.wait(timeout):
        raise AdminError(f"Вузол не відповів за {timeout} с (слабкий зв'язок чи він вимкнений; повторіть пізніше)")
    pk = box["p"]
    d = pk.get("decoded", {})
    if d.get("portnum") == "ROUTING_APP":
        reason = d.get("routing", {}).get("errorReason", "NONE")
        if reason == "ADMIN_BAD_SESSION_KEY" and retry_session:  # ключ сесії прострочено: запросити новий і повторити
            i._getOrCreateByNum(num).pop("adminSessionPassKey", None)
            ensure_session(i, target, timeout)
            return call(i, target, p, timeout, ack_ok, retry_session=False)
        if reason != "NONE":
            raise AdminError(ERRORS.get(reason, f"помилка {reason}"))
    return pk


def ensure_session(i, target, timeout):
    num = _num(i, target)
    if i._getOrCreateByNum(num).get("adminSessionPassKey") is None:
        p = admin_pb2.AdminMessage()
        p.get_config_request = admin_pb2.AdminMessage.ConfigType.SESSIONKEY_CONFIG
        call(i, target, p, timeout, retry_session=False)


# ---- розділи конфігурації ----
def read_section(i, target, section, timeout=60):
    d = _descriptor(section)  # перевірка назви
    del d
    if is_local(i, target):
        cfg = i.localNode.localConfig if section in CONFIG_SECTIONS else i.localNode.moduleConfig
        return to_values(getattr(cfg, section), section)
    p = admin_pb2.AdminMessage()
    if section in CONFIG_SECTIONS:
        p.get_config_request = admin_pb2.AdminMessage.ConfigType.Value(section.upper() + "_CONFIG")
    else:
        p.get_module_config_request = admin_pb2.AdminMessage.ModuleConfigType.Value(MODULE_SECTIONS[section])
    pk = call(i, target, p, timeout)
    raw = pk.get("decoded", {}).get("admin", {}).get("raw")
    if raw is None:
        raise AdminError("Вузол відповів не конфігурацією (перевірте, що ваш вузол є в його security.admin_key)")
    msg = raw.get_config_response if section in CONFIG_SECTIONS else raw.get_module_config_response
    if msg.WhichOneof("payload_variant") != section:
        raise AdminError("Вузол прислав інший розділ, ніж запитано")
    return to_values(getattr(msg, section), section)


def write_section(i, target, section, values, timeout=60):
    if section in READONLY:
        raise AdminError("Цей розділ лише для читання: у ньому ключі вузла (змінюйте їх у застосунку Meshtastic чи CLI)")
    is_cfg = section in CONFIG_SECTIONS
    if is_local(i, target):
        cfg = i.localNode.localConfig if is_cfg else i.localNode.moduleConfig
        cur = getattr(cfg, section)
        cur.CopyFrom(_parse(cur, section, values))
        i.localNode.writeConfig(section)
        return {"applied": True, "note": "Вузол може перезапуститися, щоб застосувати зміни."}
    ensure_session(i, target, timeout)
    cur = type(getattr(localonly_pb2.LocalConfig() if is_cfg else localonly_pb2.LocalModuleConfig(), section))()
    msg = _parse(cur, section, values)
    p = admin_pb2.AdminMessage()
    getattr(p.set_config if is_cfg else p.set_module_config, section).CopyFrom(msg)
    call(i, target, p, timeout, ack_ok=True)
    return {"applied": True, "note": "Вузол прийняв налаштування (він може перезапуститися)."}


# ---- канали ----
def _chan_dict(ch):
    s = ch.settings
    return {"index": ch.index, "role": channel_pb2.Channel.Role.Name(ch.role), "name": s.name,
            "psk": base64.b64encode(s.psk).decode(), "channel_num": s.channel_num, "id": s.id,
            "uplink_enabled": s.uplink_enabled, "downlink_enabled": s.downlink_enabled,
            "position_precision": s.module_settings.position_precision, "is_muted": s.module_settings.is_muted}


def read_channels(i, target, timeout=60, progress=None):
    if is_local(i, target):
        chans = i.localNode.channels
        if chans is None:
            raise AdminError("Канали вузла ще не завантажені — спробуйте за хвилину")
        return [_chan_dict(c) for c in chans]
    out = []
    for idx in range(8):
        if progress:
            progress(f"канал {idx + 1} з 8")
        p = admin_pb2.AdminMessage()
        p.get_channel_request = idx + 1
        pk = call(i, target, p, timeout)
        raw = pk.get("decoded", {}).get("admin", {}).get("raw")
        if raw is None:
            raise AdminError("Вузол відповів не каналом")
        out.append(_chan_dict(raw.get_channel_response))
    return out


def write_channel(i, target, data, timeout=60):
    try:
        idx = int(data["index"])
        role = channel_pb2.Channel.Role.Value(data.get("role", "SECONDARY"))
        psk = base64.b64decode(data.get("psk") or "", validate=True)
        if len(psk) not in (0, 1, 16, 32):
            raise ValueError("PSK: порожній, 1, 16 або 32 байти (base64)")
        name = str(data.get("name", ""))
        if len(name.encode()) > 11:
            raise ValueError("Назва каналу: до 11 байт")
        prec = int(data.get("position_precision", 0))
        if not 0 <= idx <= 7 or not 0 <= prec <= 32:
            raise ValueError("Некоректний номер каналу або точність позиції")
    except (KeyError, ValueError, TypeError) as e:
        raise AdminError(f"Некоректний канал: {str(e)[:120]}") from e
    ch = channel_pb2.Channel(index=idx, role=role)
    ch.settings.name, ch.settings.psk = name, psk
    ch.settings.uplink_enabled = bool(data.get("uplink_enabled"))
    ch.settings.downlink_enabled = bool(data.get("downlink_enabled"))
    ch.settings.module_settings.position_precision = prec
    ch.settings.module_settings.is_muted = bool(data.get("is_muted"))
    if is_local(i, target):
        node = i.localNode
        if node.channels is None:
            raise AdminError("Канали вузла ще не завантажені — спробуйте за хвилину")
        node.channels[idx].CopyFrom(ch)
        node.writeChannel(idx)
        return {"applied": True}
    ensure_session(i, target, timeout)
    p = admin_pb2.AdminMessage()
    p.set_channel.CopyFrom(ch)
    call(i, target, p, timeout, ack_ok=True)
    return {"applied": True}


# ---- назва, позиція, метадані ----
def read_owner(i, target, timeout=60):
    if is_local(i, target):
        u = (i.getMyNodeInfo() or {}).get("user", {})
        return {"long_name": u.get("longName"), "short_name": u.get("shortName"), "id": u.get("id")}
    p = admin_pb2.AdminMessage()
    p.get_owner_request = True
    raw = call(i, target, p, timeout).get("decoded", {}).get("admin", {}).get("raw")
    if raw is None:
        raise AdminError("Вузол відповів не даними власника")
    u = raw.get_owner_response
    return {"long_name": u.long_name, "short_name": u.short_name, "id": u.id,
            "is_licensed": u.is_licensed, "is_unmessagable": u.is_unmessagable}


def read_metadata(i, target, timeout=60):
    p = admin_pb2.AdminMessage()
    p.get_device_metadata_request = True
    raw = call(i, target, p, timeout).get("decoded", {}).get("admin", {}).get("raw")
    if raw is None:
        raise AdminError("Вузол відповів не метаданими")
    m = raw.get_device_metadata_response
    return json_format.MessageToDict(m, preserving_proto_field_name=True, always_print_fields_with_no_presence=True) \
        if hasattr(json_format, "MessageToDict") else {}


def set_owner(i, target, long_name, short_name, timeout=60):
    if not isinstance(long_name, str) or not isinstance(short_name, str):
        raise AdminError("Назва й коротка назва — рядки")
    long_name, short_name = long_name.strip(), short_name.strip()
    if not long_name or len(long_name.encode()) > 39 or not short_name or len(short_name.encode()) > 4:
        raise AdminError("Назва: 1–39 байт, коротка: 1–4 байти")
    if is_local(i, target):
        i.localNode.setOwner(long_name, short_name)
        return {"applied": True}
    ensure_session(i, target, timeout)
    p = admin_pb2.AdminMessage()
    p.set_owner.long_name, p.set_owner.short_name = long_name, short_name
    call(i, target, p, timeout, ack_ok=True)
    return {"applied": True}


def set_fixed_position(i, target, lat, lon, alt, timeout=60):
    try:
        lat, lon, alt = float(lat), float(lon), int(alt)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180 and -1000 <= alt <= 100000):
            raise ValueError
    except (TypeError, ValueError, OverflowError) as e:
        raise AdminError("Широта −90…90, довгота −180…180, висота в метрах") from e
    if is_local(i, target):
        i.localNode.setFixedPosition(lat, lon, alt)
        return {"applied": True}
    ensure_session(i, target, timeout)
    p = admin_pb2.AdminMessage()
    p.set_fixed_position.latitude_i, p.set_fixed_position.longitude_i = int(lat * 1e7), int(lon * 1e7)
    p.set_fixed_position.altitude = alt
    call(i, target, p, timeout, ack_ok=True)
    return {"applied": True}


# ---- дії над вузлом і його списком вузлів ----
ACTIONS = {  # назва -> (поле AdminMessage, тип значення)
    "reboot": ("reboot_seconds", "secs"), "shutdown": ("shutdown_seconds", "secs"),
    "factory_reset_config": ("factory_reset_config", "int1"), "nodedb_reset": ("nodedb_reset", "bool"),
    "set_time": ("set_time_only", "time"), "remove_fixed_position": ("remove_fixed_position", "bool"),
    "remove_node": ("remove_by_nodenum", "node"), "favorite": ("set_favorite_node", "node"),
    "unfavorite": ("remove_favorite_node", "node"), "ignore": ("set_ignored_node", "node"),
    "unignore": ("remove_ignored_node", "node"), "mute": ("toggle_muted_node", "node"),
}
CONFIRM = {"shutdown", "factory_reset_config", "nodedb_reset", "remove_node"}


def action(i, target, name, args, timeout=60):
    if name not in ACTIONS:
        raise AdminError("Невідома дія")
    field, kind = ACTIONS[name]
    p = admin_pb2.AdminMessage()
    if kind == "secs":
        secs = args.get("secs", 10)
        if type(secs) is not int or not 1 <= secs <= 3600:
            raise AdminError("Затримка 1..3600 с")
        setattr(p, field, secs)
    elif kind == "int1":
        setattr(p, field, 1)
    elif kind == "bool":
        setattr(p, field, True)
    elif kind == "time":
        setattr(p, field, int(time.time()))
    else:  # node
        try:
            setattr(p, field, to_node_num(args["node"]))
        except (KeyError, ValueError, TypeError) as e:
            raise AdminError("node: !xxxxxxxx") from e
    if not is_local(i, target):
        ensure_session(i, target, timeout)
    try:
        call(i, target, p, timeout, ack_ok=True)
    except AdminError:
        if name in ("reboot", "shutdown", "factory_reset_config") and not is_local(i, target):
            raise
        if is_local(i, target) and name in ("reboot", "shutdown", "factory_reset_config"):
            return {"applied": True, "note": "Команда надіслана; вузол вимикається чи перезапускається."}
        raise
    return {"applied": True}


# ---- виконання з журналом прогресу ----
def run(i, op, a, progress=None):
    """Одна операція за назвою; a — аргументи від браузера (target вже перевірено)."""
    target = a["target"]
    if op == "read":
        return {"section": a["section"], "values": read_section(i, target, a["section"])}
    if op == "write":
        return write_section(i, target, a["section"], a.get("values"))
    if op == "channels":
        return {"channels": read_channels(i, target, progress=progress)}
    if op == "write_channel":
        return write_channel(i, target, a.get("channel") or {})
    if op == "owner":
        return read_owner(i, target)
    if op == "set_owner":
        return set_owner(i, target, a.get("long_name"), a.get("short_name"))
    if op == "metadata":
        return {"metadata": read_metadata(i, target)}
    if op == "fixed_position":
        return set_fixed_position(i, target, a.get("lat"), a.get("lon"), a.get("alt", 0))
    if op == "action":
        return action(i, target, a.get("name"), a.get("args") or {})
    raise AdminError("Невідома операція")
