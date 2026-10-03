"""
mqtt_listener.py
----------------
Écoute les messages MQTT (topic dialysis/machine/#)
et les enregistre dans Django exactement comme l'endpoint HTTP /api/push/.

Usage:
  python mqtt_listener.py

Pré-requis:
  - Mosquitto qui tourne
  - Django DB accessible (même settings)
  - Une séance "En cours" pour la machine (sinon le message est ignoré)
"""

import json
import os
import sys
import django
import time

# ===================== Django setup =====================
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "PFA.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from machines.models import Machine
from seances.models import Seance
from monitoring.models import LiveMeasurement
from monitoring.services import persist_measurement_alerts

# ===================== MQTT config =====================
# Configurable via environment variables (see .env.example).
MQTT_BROKER = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TOPIC = os.environ.get("MQTT_TOPIC", "dialysis/machine/#")

# =======================================================

def save_measurement(data: dict) -> bool:
    """
    Même logique que api.views.push_measurement
    """
    try:
        machine_id = data.get("machine_id")
        if not machine_id:
            print("[ERROR] machine_id manquant")
            return False

        try:
            machine = Machine.objects.get(machine_id=machine_id)
        except Machine.DoesNotExist:
            print(f"[ERROR] Machine '{machine_id}' introuvable en base")
            return False

        seance = Seance.objects.filter(
            machine=machine,
            status="en cours"
        ).first()

        if not seance:
            print(f"[WARN] Aucune séance 'En cours' pour {machine_id} -> message ignoré")
            return False

        measurement = LiveMeasurement.objects.create(
            seance=seance,
            Debit_sang=data.get("Qb"),
            Taux_UF=data.get("UF_rate"),
            PA=data.get("PA"),
            PTM=data.get("PTM"),
            PV=data.get("PV"),
            Volume_UF=data.get("UF_volume"),
            Heparine=data.get("Heparin"),
        )

        persist_measurement_alerts(seance, measurement)

        print(f"[SAVED] LiveMeasurement {measurement.id} | Qb={data.get('Qb')} PA={data.get('PA')}")
        return True

    except Exception as e:
        print(f"[ERROR] save_measurement: {e}")
        return False


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0 or str(reason_code) == "Success":
        print(f"[MQTT] Connected to {MQTT_BROKER}:{MQTT_PORT}")
        client.subscribe(MQTT_TOPIC)
        print(f"[MQTT] Subscribed -> {MQTT_TOPIC}")
    else:
        print(f"[MQTT] Connection failed: {reason_code}")


def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode("utf-8")
        data = json.loads(payload)
        print(f"\n[MQTT] Received on {msg.topic}")
        print(f"       {payload[:120]}...")
        save_measurement(data)
    except json.JSONDecodeError:
        print("[ERROR] Invalid JSON")
    except Exception as e:
        print(f"[ERROR] on_message: {e}")


def main():
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("Install paho-mqtt:  pip install paho-mqtt")
        sys.exit(1)

    # Compatible avec les nouvelles versions de paho-mqtt
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="django-mqtt-listener")
    except AttributeError:
        client = mqtt.Client(client_id="django-mqtt-listener")

    client.on_connect = on_connect
    client.on_message = on_message

    print("=" * 50)
    print("  MQTT Listener for Django")
    print(f"  Broker : {MQTT_BROKER}:{MQTT_PORT}")
    print(f"  Topic  : {MQTT_TOPIC}")
    print("=" * 50)

    while True:
        try:
            client.connect(MQTT_BROKER, MQTT_PORT, 60)
            client.loop_forever()
        except Exception as e:
            print(f"[MQTT] Connection lost: {e} -> retry in 5s")
            time.sleep(5)


if __name__ == "__main__":
    main()