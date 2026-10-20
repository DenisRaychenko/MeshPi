"""Assemble templates/app.html (tabbed single page) from the original index.html and settings.html.

Nothing is rewritten by hand: panels are cut out of the originals by tag depth, the two inline
scripts are wrapped in their own function scope, and the result is checked for duplicate ids.
"""
import re
import sys
from pathlib import Path

src = Path(sys.argv[1])          # original webui/
dst = Path(sys.argv[2])          # webui copy to write app.html into

VOID = {"input", "br", "hr", "img", "meta", "link", "col", "source", "wbr"}
TOKEN = re.compile(r"<(/?)([a-zA-Z][\w-]*)\b[^>]*?(/?)>")


def split_top_level(html):
    """Top-level children of a fragment: [(tag, opening_tag_text, full_html)]."""
    out, depth, start, first = [], 0, None, None
    for m in TOKEN.finditer(html):
        closing, tag, selfclose = m.group(1) == "/", m.group(2).lower(), m.group(3) == "/"
        if tag in VOID or selfclose:
            if depth == 0:
                out.append((tag, m.group(0), m.group(0)))
            continue
        if not closing:
            if depth == 0:
                start, first = m.start(), (tag, m.group(0))
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                out.append((first[0], first[1], html[start:m.end()]))
    return out


def main_of(text):
    return re.search(r"<main>(.*?)</main>", text, re.S).group(1)


def inline_script(text):
    scripts = re.findall(r"<script>(.*?)</script>", text, re.S)
    # the first inline script is the theme bootstrap in <head>; the page logic is the last one
    return scripts[-1]


def h2(block):
    m = re.search(r"<h2>(.*?)</h2>", block, re.S)
    return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""


index = (src / "templates/index.html").read_text(encoding="utf8")
settings = (src / "templates/settings.html").read_text(encoding="utf8")

# --- точкові зміни логіки вузлів (кожна має збігтися рівно один раз) ---
PATCHES = [
    # колонка «Довкілля»
    ("<th>Координати</th>", "<th>Координати</th><th>Довкілля</th>"),
    ("const COLS = ['Назва', 'ID', 'SNR', 'RSSI', 'Стрибки', 'Координати', 'Відстань', 'Востаннє чутно'];",
     "const COLS = ['Назва', 'ID', 'SNR', 'RSSI', 'Стрибки', 'Координати', 'Довкілля', 'Відстань', 'Востаннє чутно'];"),
    ("[n.long || n.short, n.id, n.snr, n.rssi, n.hops, pos, distStr(n.dist), ago(n.heard)][k]",
     "[n.long || n.short, n.id, n.snr, n.rssi, n.hops, pos, envStr(n), distStr(n.dist), ago(n.heard)][k]"),
    ("      if (k === 1 && n.id) { c.title = 'Подробиці й запити до вузла'; c.onclick = () => openNode(n); }",
     "      if (k === 1 && n.id) { c.title = 'Подробиці й запити до вузла'; c.onclick = () => openNode(n); }\n"
     "      if (k === 6 && n.env_t) c.title = 'Показники ' + ago(n.env_t) + ' тому';"),
    ("function renderNodes() {",
     "// Показники довкілля вузла, якщо він їх передає (дані з бази панелі: /api/nodes -> env)\n"
     "const ENVF = [['temperature', v => v.toFixed(1) + '°C'], ['humidity', v => Math.round(v) + '%'],\n"
     "  ['pressure', v => Math.round(v) + ' гПа'], ['iaq', v => 'IAQ ' + Math.round(v)], ['lux', v => Math.round(v) + ' лк'],\n"
     "  ['wind', v => v.toFixed(1) + ' м/с'], ['pm25', v => 'PM2.5 ' + Math.round(v)], ['co2', v => Math.round(v) + ' ppm']];\n"
     "function envStr(n) {\n"
     "  if (!n.env) return null;\n"
     "  const p = ENVF.filter(([k]) => n.env[k] != null).map(([k, f]) => f(+n.env[k]));\n"
     "  return p.length ? p.join(' · ') : null;\n"
     "}\n"
     "function renderNodes() {"),
    ("  if (dist) info.push(dist);\n", "  if (dist) info.push(dist);\n  const es = envStr(n); if (es) info.push(es);\n"),
    # фільтр «без MQTT»
    ('<label><input type="checkbox" id="favonly"> лише обрані ★</label>',
     '<label><input type="checkbox" id="favonly"> лише обрані ★</label>\n'
     '      <label title="Сховати вузли, відомі лише через MQTT-міст, а не з радіоефіру"><input type="checkbox" id="nomqtt" checked> без MQTT</label>'),
    ("    if ($('#pos').checked && (n.lat == null || n.lon == null)) continue;\n",
     "    if ($('#pos').checked && (n.lat == null || n.lon == null)) continue;\n"
     "    if ($('#nomqtt').checked && n.mqtt && n.id !== myId && !favs.has(n.id)) continue;\n"),
    ("for (const id of ['#q', '#favonly', '#pos', '#age']) $(id).addEventListener('input', renderNodes);",
     "for (const id of ['#q', '#favonly', '#nomqtt', '#pos', '#age']) $(id).addEventListener('input', renderNodes);\n"
     "try { const m = localStorage.getItem('nomqtt'); if (m != null) $('#nomqtt').checked = m === '1'; } catch (e) { /* ок */ }\n"
     "$('#nomqtt').addEventListener('change', () => { try { localStorage.setItem('nomqtt', $('#nomqtt').checked ? '1' : '0'); } catch (e) { /* ок */ } });"),
]
for old, new in PATCHES:
    assert index.count(old) == 1, f"patch target must match exactly once: {old[:70]!r} (found {index.count(old)})"
    index = index.replace(old, new)

ENV_PANEL = """<section class="panel" id="envbox">
    <h2 id="envtitle">Довкілля</h2>
    <p id="envnote" class="hint" style="margin:0 0 12px"></p>
    <div id="envgrid" class="envgrid"></div>
    <details id="envsrc" hidden style="margin-top:12px">
      <summary class="hint" style="cursor:pointer">Джерела даних</summary>
      <div class="table-wrap"><table><thead><tr id="envsrchead"></tr></thead><tbody id="envsrcbody"></tbody></table></div>
    </details>
  </section>"""

ib = split_top_level(main_of(index))
sb = split_top_level(main_of(settings))


def find(blocks, *, title=None, id_=None, cls=None):
    for tag, opening, full in blocks:
        if title and h2(full).startswith(title):
            return full
        if id_ and f'id="{id_}"' in opening:
            return full
        if cls and re.search(rf'class="[^"]*\b{cls}\b', opening):
            return full
    raise SystemExit(f"block not found: {title or id_ or cls}")


panes = {
    "overview": [find(ib, id_="stats"), find(ib, id_="sys"), ENV_PANEL, find(ib, cls="strip"),
                 find(ib, id_="errs"), find(ib, id_="chartsbox")],
    "messages": [find(ib, title="Повідомлення")],
    "nodes": [find(ib, title="Вузли"), find(ib, id_="mapbox")],
    "radio": [find(sb, id_="pwr"), find(sb, title="Радіо"), find(sb, title="Потужність"),
              find(sb, title="Пошук мережі"), find(sb, title="Канали")],
    "node": [find(sb, title="Вузол"), find(sb, title="Розташування"),
             find(sb, title="Автовідповідь"), find(sb, title="Мелодія")],
    "system": [find(ib, title="Журнал"), find(sb, title="Пароль"), find(sb, title="Резервна копія")],
}
offline = find(sb, id_="offline")

used = {id(b) for b in []}
all_blocks = [b for v in panes.values() for b in v] + [offline]
missing = [t for tag, o, full in ib + sb if full not in all_blocks
           for t in [f"{tag} {o[:60]}"]]
print("blocks not placed in any tab:", missing or "none")

TABS = [("overview", "Огляд"), ("messages", "Повідомлення"), ("nodes", "Вузли"),
        ("radio", "Радіо"), ("node", "Вузол"), ("system", "Система")]

head = re.search(r"<head>(.*?)</head>", index, re.S).group(1).replace("<title>Meshpi</title>", "<title>Meshpi</title>")
header_html = f"""<header>
  <div class="brand"><div class="ring"><span class="ring-dot"></span></div>
    <div><h1>Meshpi</h1><p id="me">Meshtastic · Raspberry Pi</p></div></div>
  <div class="row"><span id="pill" class="status-pill"><span class="dot"></span><span id="pilltxt">…</span></span>
    <button type="button" id="theme" class="settings-link" title="Світла/темна тема">☾</button>
    <form method="post" action="/logout" style="margin:0"><button class="check-btn" title="Вийти з панелі">Вийти</button></form></div>
</header>
<nav id="tabbar" class="tabbar" role="tablist" aria-label="Розділи">
""" + "\n".join(f'  <button type="button" role="tab" data-tab="{k}">{v}</button>' for k, v in TABS) + "\n</nav>"

body_panes = "\n".join(
    f'  <div class="tabpane" data-tab="{k}" role="tabpanel" hidden>\n' + "\n".join(v) + "\n  </div>"
    for k, v in panes.items())

page = f"""<!doctype html>
<html lang="uk">
<head>{head}</head>
<body>
<div class="wrap">
{header_html}
<main>
  {offline}
{body_panes}
</main>
<footer class="hint foot">Meshpi © 2026 JunioR · ліцензія MIT</footer>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script src="/static/logic.js"></script>
<script src="/static/common.js"></script>
<script src="/static/tabs.js"></script>
<script>(function () {{  // сторінка «головна» (власна область видимості)
{inline_script(index)}
}})();</script>
<script>(function () {{  // сторінка «налаштування» (власна область видимості)
{inline_script(settings)}
}})();</script>
<script src="/static/env.js"></script>
</body>
</html>
"""

ids = re.findall(r'\bid="([^"]+)"', page)
dups = sorted({i for i in ids if ids.count(i) > 1})
print("duplicate ids:", dups or "none")
(dst / "templates").mkdir(parents=True, exist_ok=True)
(dst / "templates/app.html").write_text(page, encoding="utf8")
print("written app.html:", len(page), "bytes;", "panes:", {k: len(v) for k, v in panes.items()})
