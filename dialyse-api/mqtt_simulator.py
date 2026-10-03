"""
mqtt_simulator.py
-----------------
Simulateur MQTT : publie sur le topic `dialysis/machine/{machine_id}`
exactement le même payload que le vrai client Edge (edge_client.py) ou le
Raspberry Pi via producer.py.

Le payload suit le protocole MQTT défini pour le projet :
  topic   : dialysis/machine/{machine_id}
  payload : {"machine_id", "Qb", "UF_rate", "PA", "PTM", "PV",
             "UF_volume", "Heparin", "timestamp", "_source", "raspi_id"}

Usage:
  python mqtt_simulator.py                 # machine M001 (ou $MACHINE_ID)
  python mqtt_simulator.py --once           # envoie un seul message puis s'arrête
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

BROKER = os.environ.get("MQTT_HOST", "localhost")
PORT = int(os.environ.get("MQTT_PORT", "1883"))
TOPIC_PREFIX = os.environ.get("MQTT_TOPIC_PREFIX", "dialysis/machine")
MACHINE_ID = os.environ.get("MACHINE_ID", "M001")
RASPI_ID = os.environ.get("RASPI_ID", "RASPI-SIM")
INTERVAL = int(os.environ.get("SIM_INTERVAL", "3"))


def build_payload() -> dict:
    """Même structure que generate_simulated_reading() d'edge_client.py."""
    return {
        "machine_id": MACHINE_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "Qb": 280,
        "UF_rate": 500,
        "PA": 220,
        "PTM": 70,
        "PV": 200,
        "UF_volume": 500,
        "Heparin": 5,
        "_source": "simulation",
        "raspi_id": RASPI_ID,
    }


def main(once: bool = False):
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mqtt-simulator")
    except AttributeError:
        client = mqtt.Client(client_id="mqtt-simulator")

    client.connect(BROKER, PORT, 60)
    # Démarre la boucle réseau MQTT : sans elle, CONNACK/PUBACK ne sont jamais
    # traités et le message en cours de sortie peut être perdu à la fermeture
    # du socket (bug constaté avec --once : message perdu dans ~50 % des cas).
    client.loop_start()

    topic = f"{TOPIC_PREFIX}/{MACHINE_ID}"
    print(f"[MQTT] Connected to {BROKER}:{PORT}")
    print(f"[MQTT] Publishing to {topic} (interval {INTERVAL}s)")

    while True:
        message = json.dumps(build_payload())
        info = client.publish(topic, message, qos=1)
        print("MQTT SENT:")
        print(message)
        print()
        if once:
            # QoS 1 : attendre l'accusé de réception du broker avant de sortir,
            # sinon la fermeture du process peut abandonner le message.
            try:
                info.wait_for_publish(timeout=5)
            except Exception as exc:
                print(f"[MQTT] Publish not confirmed: {exc}")
            break
        time.sleep(INTERVAL)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MQTT simulator for dialysis readings")
    parser.add_argument("--once", action="store_true", help="Send a single message then exit")
    args = parser.parse_args()
    main(once=args.once)