#!/usr/bin/env python3
"""mesh-radio-agent: крихітний HTTP-агент для Raspberry Pi з meshtasticd. Лише читання.

Панель у Docker на іншому хості не бачить журналу й конфігів meshtasticd, тож не знає, яку потужність радіо насправді
виставило на виході чипа. Агент працює на самій Pi й віддає ці три значення панелі:

  GET /radio.json   {"logged": 10, "final": 10, "cap": 22, "radio": "lora-e22-900m22s.yaml", "cached": false, "t": 1792700000}
      logged  потужність після ліміту регіону (рядок «Set radio: … power=N» у журналі meshtasticd). Рядок пишеться
              лише при старті радіо й за кілька годин зникає з журналу в пам'яті, тому агент раз на хвилину дивиться
              в журнал і запам'ятовує останнє значення (файл стану); «cached»: true — значення з пам'яті агента,
              чинне, доки meshtasticd не перезапущено
      final   потужність, яку прошивка справді виставила чипу («Final Tx power: N»)
      cap     стеля чипа з конфігу (SX126X_MAX_POWER / RF95_MAX_POWER у /etc/meshtasticd/config.yaml чи config.d/*.yaml;
              22, якщо конфіги є, а ключа в них немає)
      radio   ім'я конфігу, де знайдено стелю (за ним панель впізнає модуль)
  GET /ping         «ok» без даних (перевірка, що агент живий)

Захист: токен у заголовку `Authorization: Bearer …` (обов'язковий), необов'язковий список дозволених IP клієнтів.
Агент нічого не пише, не виконує чужих команд (журнал читається однією фіксованою командою) і не віддає нічого, крім цих полів.

Змінні середовища: AGENT_TOKEN (обов'язково), AGENT_PORT (8097), AGENT_BIND (0.0.0.0), AGENT_ALLOW (IP через кому),
AGENT_UNIT (meshtasticd), AGENT_CFG_GLOB (шляхи конфігів через «:», типово config.yaml і config.d/*.yaml).
"""
import glob
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("AGENT_PORT", "8097"))
BIND = os.environ.get("AGENT_BIND", "0.0.0.0")
TOKEN = os.environ.get("AGENT_TOKEN", "")
ALLOW = {x.strip() for x in os.environ.get("AGENT_ALLOW", "").split(",") if x.strip()}
UNIT = os.environ.get("AGENT_UNIT", "meshtasticd")
# де шукати стелю чипа: головний конфіг і додаткові (кілька шляхів через os.pathsep, тобто «:» на Linux)
CFG_GLOB = os.environ.get("AGENT_CFG_GLOB", os.pathsep.join(["/etc/meshtasticd/config.yaml", "/etc/meshtasticd/config.d/*.yaml"]))
CACHE_SECS = 10  # journalctl на Pi важкий: не частіше, ніж раз на кілька секунд

POWER_RE = re.compile(r"Set radio:.*power=(-?\d+)")
FINAL_RE = re.compile(r"Final Tx power: (-?\d+)")
CAP_RE = re.compile(r"^\s*(?:SX126X|RF95)_MAX_POWER:\s*(\d+)", re.M)

STATE = os.environ.get("AGENT_STATE", "/var/lib/mesh-radio-agent/state.json")  # запам'ятовані значення (переживають і зникнення рядків з журналу)
POLL_SECS = 60  # як часто фоном дивитись у журнал: рядок «Set radio» там живе лише години (журнал у пам'яті, старе стирається)

_cache: tuple = (0.0, {})
_lock = threading.Lock()
_state: dict = {}


def journal_text():
    """Лише потрібні рядки за поточне завантаження; журнал читається однією фіксованою командою."""
    try:
        return subprocess.run(["journalctl", "-u", UNIT, "-b", "--no-pager", "-o", "cat", "-g", "Set radio|Final Tx power",
                               "-n", "10", "-r"], capture_output=True, text=True, errors="replace", timeout=8).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def chip_cap():
    """Стеля чипа й ім'я конфігу, де її задано (останній знайдений); (None, None), якщо конфігів немає."""
    cap, radio, seen = None, None, False
    for f in [p for pat in CFG_GLOB.split(os.pathsep) if pat for p in sorted(glob.glob(pat))]:
        try:
            text = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        seen = True
        found = CAP_RE.findall(text)
        if found:
            cap, radio = int(found[-1]), os.path.basename(f)
    return (cap if cap is not None else (22 if seen else None)), radio  # 22 — типова стеля, якщо ключа в конфігах немає


def start_key():
    """Ідентифікатор запуску meshtasticd (завантаження + момент старту служби): змінився — радіо ініціалізовано заново."""
    try:
        boot = open("/proc/sys/kernel/random/boot_id").read().strip()
        mono = subprocess.run(["systemctl", "show", UNIT, "-p", "ActiveEnterTimestampMonotonic", "--value"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
        return f"{boot}:{mono}" if mono else None
    except (OSError, subprocess.SubprocessError):
        return None


def load_state():
    global _state
    try:
        with open(STATE, encoding="utf-8") as fh:
            s = json.load(fh)
        _state = {k: s.get(k) for k in ("key", "logged", "final", "seen")}
    except (OSError, ValueError):
        _state = {}


def save_state():
    try:
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_state, fh)
        os.replace(tmp, STATE)
    except OSError:
        pass  # без каталогу стану працюємо лише з пам'яттю


def refresh():
    """Прочитати журнал і оновити запам'ятоване. Рядок «Set radio» з'являється лише при старті радіо й за кілька годин
    стирається з журналу, тож останнє побачене значення зберігаємо — доки meshtasticd не перезапустили."""
    text = journal_text()
    logged, final = POWER_RE.findall(text), FINAL_RE.findall(text)  # найновіші першими (-r)
    key = start_key()
    if key and _state.get("key") not in (None, key):  # meshtasticd перезапущено: старі значення недійсні
        _state.update(logged=None, final=None, seen=None)
    if key:
        _state["key"] = key
    if logged:
        _state["logged"] = int(logged[0])
    if final:
        _state["final"] = int(final[0])
    if logged or final:
        _state["seen"] = int(time.time())
    save_state()
    return bool(logged or final)


def radio_info():
    global _cache
    with _lock:
        if time.time() - _cache[0] < CACHE_SECS:
            return _cache[1]
        fresh = refresh()
        cap, radio = chip_cap()
        info = {"logged": _state.get("logged"), "final": _state.get("final"), "cap": cap, "radio": radio,
                "cached": bool(_state.get("seen")) and not fresh, "seen": _state.get("seen"), "t": int(time.time())}
        _cache = (time.time(), info)
        return info


def poll_loop():
    while True:
        time.sleep(POLL_SECS)
        try:
            with _lock:
                refresh()
        except Exception as exc:  # noqa: BLE001 — фоновий потік не має падати
            print(f"poll: {exc}", file=sys.stderr, flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "mesh-radio-agent"
    sys_version = ""

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/ping":
            return self._send(200, b"ok", "text/plain")
        if ALLOW and self.client_address[0] not in ALLOW:
            return self._send(403, {"error": "forbidden"})
        got = self.headers.get("Authorization", "")
        if not TOKEN or not got.startswith("Bearer ") or not hmac.compare_digest(got[7:].encode(), TOKEN.encode()):
            return self._send(401, {"error": "unauthorized"})
        if self.path != "/radio.json":
            return self._send(404, {"error": "not found"})
        self._send(200, radio_info())

    do_POST = do_PUT = do_DELETE = do_PATCH = lambda self: self._send(405, {"error": "read only"})  # noqa: E731

    def log_message(self, fmt, *args):  # без токенів і зайвого шуму в журналі
        pass


def main():
    if not TOKEN or len(TOKEN) < 16:
        sys.exit("AGENT_TOKEN не задано або закороткий (потрібно хоча б 16 символів)")
    load_state()
    threading.Thread(target=poll_loop, daemon=True).start()
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    srv.daemon_threads = True
    print(f"mesh-radio-agent слухає {BIND}:{PORT}" + (f", дозволені клієнти: {', '.join(sorted(ALLOW))}" if ALLOW else ""), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
