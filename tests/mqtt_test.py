"""MQTT status of the node:  python tests/mqtt_test.py webui"""
import json
import os
import sys
import tempfile
import types

os.environ["MESHPI_DB"] = os.path.join(tempfile.mkdtemp(), "messages.db")
os.environ.pop("MESHPI_PASSWORD", None)
sys.path.insert(0, sys.argv[1])
import app as m

ns = types.SimpleNamespace
ok = True


def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print("ok  " if cond else "FAIL", name, extra)


def node(enabled, address="mqtt.meshtastic.org", channels=None):
    mq = ns(enabled=enabled, address=address, root="msh/EU_433", proxy_to_client_enabled=False,
            map_reporting_enabled=False, tls_enabled=False, json_enabled=False,
            username="secret-user", password="secret-pass", encryption_enabled=True)
    chans = channels if channels is not None else [
        ns(index=0, role=1, settings=ns(name="", uplink_enabled=True, downlink_enabled=True)),
        ns(index=1, role=2, settings=ns(name="Private", uplink_enabled=False, downlink_enabled=True)),
        ns(index=2, role=0, settings=ns(name="off", uplink_enabled=True, downlink_enabled=True)),   # DISABLED
    ]
    return ns(localNode=ns(moduleConfig=ns(mqtt=mq), channels=chans))


off = m.mqtt_info(node(False))
check("disabled module reported as disabled", off["enabled"] is False)
check("only active channels are listed", [c["index"] for c in off["channels"]] == [0, 1])
check("unnamed channel gets a fallback name", off["channels"][0]["name"] == "канал 0")
check("uplink/downlink flags per channel", off["channels"][0]["uplink"] and not off["channels"][1]["uplink"]
      and off["channels"][1]["downlink"])
on = m.mqtt_info(node(True))
check("enabled module reported with server and root", on["enabled"] and on["address"] == "mqtt.meshtastic.org"
      and on["root"] == "msh/EU_433")
dump = json.dumps(on)
check("credentials never leave the server", "secret-user" not in dump and "secret-pass" not in dump
      and "username" not in on and "password" not in on)
check("modules not loaded yet -> None (no strip)", m.mqtt_info(ns(localNode=ns())) is None)
check("no channels yet -> empty list, no crash", m.mqtt_info(node(False, channels=[]))["channels"] == [])

page = m.app.test_client().get("/").get_data(as_text=True)
a = page.index('class="tabpane" data-tab="nodes"')
b = page.index('class="tabpane" data-tab="radio"')   # the pane after "nodes"
check("MQTT strip sits on the Nodes tab", a < page.index('id="mqttstrip"') < b)
check("and only once", page.count('id="mqttstrip"') == 1)
check("per-node 'Over MQTT' column exists and is sortable", '<th data-s="mqtt"' in page and "'MQTT', 'Востаннє чутно'" in page)
check("the cell value follows the node flag", "n.mqtt ? 'так' : 'ні'" in page)
check("node details say where the node was heard", "n.mqtt ? 'через MQTT' : 'з радіоефіру'" in page)

m.init_db(m.DB_PATH)
m.use_iface(ns(**{**node(True).__dict__, "nodesByNum": {}, "nodes": {}}, getMyNodeInfo=lambda: {"user": {"id": "!1"}, "deviceMetrics": {}}))
m.iface.localNode.localConfig = ns(lora=ns(hop_limit=5, channel_num=1, region=2, modem_preset=0, tx_power=10,
                                          override_frequency=0.0))
j = m.app.test_client().get("/api/status").get_json()
check("/api/status carries mqtt", j.get("mqtt", {}).get("enabled") is True and j["mqtt"]["channels"][0]["index"] == 0,
      str(j.get("mqtt", {}).get("address")))
sys.exit(0 if ok else 1)
