#!/usr/bin/env python3
"""Two-way Telegram bridge for the Meshtastic node.

mesh -> Telegram: new messages and alerts, read from the SQLite file written by
                  mesh-logger / the screen app (no extra node client needed for this).
Telegram -> mesh: replies and commands, sent to the node through mesh-proxy (TCP 4403).

Telegram commands (only from TG_CHAT_ID):
  reply to a bot message   answer that sender privately (or the channel, if it was a channel message)
  /ch <text>               send to the LongFast channel
  /dm <name> <text>        private message to a node (short or long name)
  /nodes                   who is on air right now
  /status                  network summary
  /help

Environment:
  TG_TOKEN, TG_CHAT_ID     bot token and the one chat that may talk to the node
  TG_CHANNEL               1 (default) forward channel messages, 0 only private ones + alerts
  TG_DRY                   1 = print instead of sending to Telegram (testing)
  TG_BACKFILL              on a fresh state, replay this many latest rows
  MESH_DB, MESH_HOST, MESH_PORT
"""
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DB = os.environ.get("MESH_DB", os.path.expanduser("~/.local/share/mesh/messages.db"))
STATE = os.environ.get("TG_STATE", os.path.expanduser("~/.local/share/mesh/telegram-state.json"))
TOKEN = os.environ.get("TG_TOKEN", "")
CHAT_ID = os.environ.get("TG_CHAT_ID", "")
CHANNEL = os.environ.get("TG_CHANNEL", "1") == "1"
DRY = os.environ.get("TG_DRY") == "1"
BACKFILL = int(os.environ.get("TG_BACKFILL", "0"))
MESH_HOST = os.environ.get("MESH_HOST", "127.0.0.1")
MESH_PORT = int(os.environ.get("MESH_PORT", "4403"))

ICON = {"fav_off": "⚠️", "link_lost": "⚠️", "fav_on": "✅", "link_ok": "✅", "new": "✚"}
HELP = ("Команди:\n"
        "• відповідь (reply) на повідомлення бота: відповісти тому відправнику\n"
        "• /ch <текст>: у канал LongFast\n"
        "• /dm <ім'я> <текст>: приватно вузлу\n"
        "• /nodes: кого зараз чути\n"
        "• /status: стан мережі")

state_lock = threading.Lock()
state = {"msg": 0, "ev": 0, "upd": 0}
db_lock = threading.Lock()


# ---------------------------------------------------------------- state / db
def load_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            s = json.load(fh)
        return {"msg": int(s["msg"]), "ev": int(s["ev"]), "upd": int(s.get("upd", 0))}
    except (OSError, ValueError, KeyError):
        return None


def save_state():
    """Atomic + fsync: a power cut must not re-send (or lose) the whole history."""
    with state_lock:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, STATE)


def open_db():
    db = sqlite3.connect(DB, timeout=10, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS tg_map (tg_id INTEGER PRIMARY KEY, node_id TEXT, dm INTEGER)")
    db.commit()
    return db


# ------------------------------------------------------------------ telegram
def tg_api(method, params, timeout=15):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def tg_send(text, reply_to=None):
    """Returns (ok, telegram_message_id)."""
    if DRY:
        print("DRY>", text.replace("\n", " ⏎ "), flush=True)
        return True, None
    params = {"chat_id": CHAT_ID, "text": text[:4000]}
    if reply_to:
        params["reply_to_message_id"] = reply_to
    for attempt in range(3):
        try:
            res = tg_api("sendMessage", params)
            time.sleep(1.1)           # Telegram: ~1 message per second per chat
            return True, res["result"]["message_id"]
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(5)
            elif 400 <= exc.code < 500:
                print(f"telegram rejected message: HTTP {exc.code}", file=sys.stderr, flush=True)
                return True, None     # do not retry forever on a bad request
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            print(f"telegram send failed ({exc}), retry {attempt + 1}", file=sys.stderr, flush=True)
        time.sleep(2 * (attempt + 1))
    return False, None


# --------------------------------------------------------------------- mesh
class Mesh:
    """Lazy, self-healing connection to the node through mesh-proxy."""

    def __init__(self):
        self.iface = None
        self.lock = threading.Lock()

    def _connect(self):
        from meshtastic.tcp_interface import TCPInterface
        if self.iface is not None:
            try:
                self.iface.close()
            except Exception:
                pass
        self.iface = TCPInterface(hostname=MESH_HOST, portNumber=MESH_PORT)

    def get(self):
        with self.lock:
            if self.iface is None:
                self._connect()
            return self.iface

    def send(self, text, dest=None):
        for attempt in (1, 2):
            try:
                iface = self.get()
                if dest:
                    iface.sendText(text, destinationId=dest, wantAck=True, channelIndex=0)
                else:
                    iface.sendText(text, channelIndex=0)
                return True
            except Exception as exc:
                print(f"mesh send failed ({exc}), reconnecting", file=sys.stderr, flush=True)
                with self.lock:
                    self.iface = None
        return False

    def nodes(self):
        iface = self.get()
        me = iface.myInfo.my_node_num if iface.myInfo else None
        out = []
        for n in (iface.nodes or {}).values():
            u = n.get("user", {})
            out.append({"id": u.get("id"), "short": u.get("shortName") or "", "long": u.get("longName") or "",
                        "snr": n.get("snr"), "hops": n.get("hopsAway"), "heard": n.get("lastHeard") or 0,
                        "me": n.get("num") == me, "mqtt": bool(n.get("viaMqtt"))})
        return out


mesh = Mesh()


def find_node(name):
    q = name.strip().lower()
    nodes = [n for n in mesh.nodes() if not n["me"] and n["id"]]
    for key in ("short", "long"):
        exact = [n for n in nodes if n[key].lower() == q]
        if len(exact) == 1:
            return exact[0], None
        if len(exact) > 1:
            return None, f"Кілька вузлів з іменем «{name}», уточни: " + ", ".join(
                f"{n['short']} ({n['long']})" for n in exact[:5])
    part = [n for n in nodes if q in n["short"].lower() or q in n["long"].lower()]
    if len(part) == 1:
        return part[0], None
    if part:
        return None, "Підходить кілька вузлів: " + ", ".join(n["short"] or n["long"] for n in part[:8])
    return None, f"Вузол «{name}» не знайдено"


def record_own(db, text, dest, dest_name):
    """Make messages sent from Telegram show up on the screen and in the history."""
    with db_lock:
        db.execute("INSERT INTO messages(t,from_name,to_id,to_name,text,dm,own) VALUES (?,?,?,?,?,?,1)",
                   (time.time(), "я (Telegram)", dest or "^all", dest_name, text, int(bool(dest))))
        db.commit()


# ----------------------------------------------------------------- commands
def parse_command(text, reply):
    """Pure parser. reply: (node_id, dm) of the bot message being replied to, or None.
    Returns (action, args...)."""
    text = (text or "").strip()
    if not text:
        return ("ignore",)
    if text.startswith("/"):
        head, _, rest = text.partition(" ")
        cmd = head.split("@")[0].lower()
        rest = rest.strip()
        if cmd in ("/start", "/help"):
            return ("help",)
        if cmd == "/nodes":
            return ("nodes",)
        if cmd == "/status":
            return ("status",)
        if cmd == "/ch":
            return ("channel", rest) if rest else ("error", "Напиши текст після /ch")
        if cmd == "/dm":
            name, _, body = rest.partition(" ")
            if not name or not body.strip():
                return ("error", "Формат: /dm <ім'я> <текст>")
            return ("dm_name", name, body.strip())
        return ("error", "Невідома команда. /help")
    if reply:
        node_id, dm = reply
        if dm and node_id:
            return ("dm_id", node_id, text)
        return ("channel", text)
    return ("error", "Щоб надіслати в мережу: /ch <текст>, /dm <ім'я> <текст> або відповідь (reply) на повідомлення бота.")


def ago(age):
    if age < 90:
        return "зараз"
    if age < 3600:
        return f"{int(age // 60)} хв"
    if age < 86400:
        return f"{int(age // 3600)} год"
    return f"{int(age // 86400)} дн"


def handle(db, text, reply, reply_to_id):
    action = parse_command(text, reply)
    kind = action[0]
    if kind == "ignore":
        return
    if kind == "help":
        tg_send(HELP, reply_to_id)
    elif kind == "error":
        tg_send(action[1], reply_to_id)
    elif kind == "nodes":
        now = time.time()
        rows = sorted((n for n in mesh.nodes()
                       if not n["me"] and not n["mqtt"] and n["heard"] and now - n["heard"] < 3600),
                      key=lambda n: -n["heard"])
        lines = [f"{n['short'] or n['long']}: SNR {n['snr']:.0f}, хопів {n['hops']}, {ago(now - n['heard'])}"
                 if n["snr"] is not None and n["hops"] is not None
                 else f"{n['short'] or n['long']}: {ago(now - n['heard'])}" for n in rows[:20]]
        tg_send("Зараз у ефірі (за годину): " + str(len(rows)) + "\n" + "\n".join(lines) if rows
                else "За останню годину нікого не чути.", reply_to_id)
    elif kind == "status":
        now = time.time()
        others = [n for n in mesh.nodes() if not n["me"] and not n["mqtt"]]
        on1 = sum(1 for n in others if n["heard"] and now - n["heard"] < 3600)
        on24 = sum(1 for n in others if n["heard"] and now - n["heard"] < 86400)
        tg_send(f"Нода на зв'язку.\nОнлайн за годину: {on1}\nЗа добу: {on24}\nУсього в базі ноди: {len(others)}",
                reply_to_id)
    elif kind in ("channel", "dm_id", "dm_name"):
        dest = dest_name = None
        if kind == "channel":
            body = action[1]
        elif kind == "dm_id":
            dest, body = action[1], action[2]
            node = next((n for n in mesh.nodes() if n["id"] == dest), None)
            dest_name = (node["short"] or node["long"]) if node else dest
        else:
            node, err = find_node(action[1])
            if err:
                tg_send(err, reply_to_id)
                return
            dest, body = node["id"], action[2]
            dest_name = node["short"] or node["long"]
        if len(body.encode("utf-8")) > 200:
            tg_send(f"Задовге: {len(body.encode('utf-8'))} байт, максимум 200.", reply_to_id)
            return
        if mesh.send(body, dest):
            record_own(db, body, dest, dest_name)
            tg_send(f"✓ надіслано {'приватно → ' + dest_name if dest else 'у канал LongFast'}", reply_to_id)
        else:
            tg_send("✗ не вдалося надіслати: немає зв'язку з нодою", reply_to_id)


def poll_loop(db):
    """Telegram -> mesh. Long-polls getUpdates; ignores everyone except CHAT_ID."""
    while True:
        try:
            res = tg_api("getUpdates", {"offset": state["upd"] + 1, "timeout": 25}, timeout=40)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            print(f"getUpdates failed: {exc}", file=sys.stderr, flush=True)
            time.sleep(5)
            continue
        for upd in res.get("result", []):
            state["upd"] = max(state["upd"], upd["update_id"])
            msg = upd.get("message") or {}
            if str((msg.get("chat") or {}).get("id")) != str(CHAT_ID):
                continue
            reply = None
            rid = (msg.get("reply_to_message") or {}).get("message_id")
            if rid:
                with db_lock:
                    row = db.execute("SELECT node_id,dm FROM tg_map WHERE tg_id=?", (rid,)).fetchone()
                reply = (row[0], bool(row[1])) if row else None
            try:
                handle(db, msg.get("text"), reply, msg.get("message_id"))
            except Exception as exc:
                print(f"command failed: {exc}", file=sys.stderr, flush=True)
                tg_send("✗ помилка: " + str(exc)[:200], msg.get("message_id"))
            save_state()


# ------------------------------------------------------------- mesh -> tg
def fmt_message(row):
    _, t, frm, to, text, dm, snr, hops = row[:8]
    meta = " · ".join(x for x in (f"SNR {snr:.0f}" if snr is not None else "",
                                  f"хопів {hops}" if hops is not None else "") if x)
    head = f"✉️ Приватне · {frm}" if dm else f"📡 {frm}"
    return f"{head}\n{text}" + (f"\n{meta}" if meta else "")


def forward_loop(db):
    while True:
        try:
            with db_lock:
                rows = db.execute("SELECT id,t,from_name,to_name,text,dm,snr,hops,from_id FROM messages "
                                  "WHERE id>? AND own=0 ORDER BY id", (state["msg"],)).fetchall()
                events = db.execute("SELECT id,kind,text FROM events WHERE id>? ORDER BY id",
                                    (state["ev"],)).fetchall()
        except sqlite3.Error as exc:
            print(f"db error: {exc}", file=sys.stderr, flush=True)
            time.sleep(5)
            continue
        for row in rows:
            if row[5] or CHANNEL:
                ok, tg_id = tg_send(fmt_message(row))
                if not ok:
                    break
                if tg_id and row[8]:
                    with db_lock:
                        db.execute("INSERT OR REPLACE INTO tg_map(tg_id,node_id,dm) VALUES (?,?,?)",
                                   (tg_id, row[8], int(bool(row[5]))))
                        db.commit()
            state["msg"] = row[0]
            save_state()
        for eid, kind, text in events:
            ok, _ = tg_send(f"{ICON.get(kind, '•')} {text}")
            if not ok:
                break
            state["ev"] = eid
            save_state()
        time.sleep(2)


def main():
    if not DRY and (not TOKEN or not CHAT_ID):
        sys.exit("TG_TOKEN and TG_CHAT_ID are required")
    db = open_db()
    loaded = load_state()
    if loaded is None:
        top_m = db.execute("SELECT COALESCE(MAX(id),0) FROM messages").fetchone()[0]
        top_e = db.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
        loaded = {"msg": max(top_m - BACKFILL, 0), "ev": top_e, "upd": 0}
    state.update(loaded)
    save_state()
    print(f"bridge started at msg={state['msg']} ev={state['ev']} upd={state['upd']}", flush=True)
    if not DRY:
        threading.Thread(target=poll_loop, args=(db,), daemon=True).start()
    forward_loop(db)


if __name__ == "__main__":
    main()
