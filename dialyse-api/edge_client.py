"""
edge_client.py
--------------
Nouveau client Edge pour le système de dialyse.
- Ne touche PAS à producer.py (code existant protégé)
- Support MQTT (principal) + Offline buffer + HTTP fallback
- Mode SIMULATION (sans Raspberry / sans caméra) pour tester maintenant

Usage:
  python edge_client.py                  # mode simulation
  python edge_client.py --real           # mode réel (quand le matériel arrive)
  python edge_client.py --mqtt-only      # force MQTT seulement
"""

import argparse
import json
import os
import random
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

# ===================== CONFIG =====================
# All values are configurable via environment variables (see .env.example).
# No hardware IDs, credentials or URLs are hardcoded.
RASPI_ID = os.environ.get("RASPI_ID", "RASPI-02")
MACHINE_ID_SIM = os.environ.get("MACHINE_ID", "M001")  # used in simulation

# MQTT
MQTT_BROKER = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_TOPIC_PREFIX = os.environ.get("MQTT_TOPIC_PREFIX", "dialysis/machine")

# HTTP fallback (Django) — même endpoint REST que /api/push/
DJANGO_BASE_URL = os.environ.get("DJANGO_BASE_URL", "http://127.0.0.1:8000")
DJANGO_PUSH_URL = f"{DJANGO_BASE_URL}/api/push/"
HEARTBEAT_URL = f"{DJANGO_BASE_URL}/machines/raspi/heartbeat/"
DEBIT_API = f"{DJANGO_BASE_URL}/api/seance/debit/"
LOCAL_AI_API = os.environ.get("AI_API_URL", "http://127.0.0.1:8001/analyze/")

# Authentification machine → Django (endpoints /api/push/ et
# /machines/raspi/heartbeat/). Même variable que le serveur (voir .env.example).
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "")
DEVICE_HEADERS = {"X-Device-Token": DEVICE_TOKEN}

# Offline
OFFLINE_DB = Path(__file__).parent / "offline_buffer.db"
DEFAULT_INTERVAL = int(os.environ.get("RASPI_DEFAULT_DEBIT", "15"))  # seconds between readings in sim mode
IMAGE_PATH = os.environ.get("RASPI_IMAGE_PATH", "frame.jpg")

# ==================================================

def init_offline_db():
    """Create the offline queue table if it does not exist."""
    conn = sqlite3.connect(OFFLINE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            attempts INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()
    print(f"[OFFLINE] Buffer ready -> {OFFLINE_DB}")


def save_offline(payload: dict):
    """Store a failed message for later retry."""
    conn = sqlite3.connect(OFFLINE_DB)
    conn.execute(
        "INSERT INTO pending (payload, created_at) VALUES (?, ?)",
        (json.dumps(payload), datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    conn.close()
    print("[OFFLINE] Message saved for later")


def flush_offline(mqtt_client=None, use_http=False):
    """Try to send all pending messages."""
    conn = sqlite3.connect(OFFLINE_DB)
    rows = conn.execute("SELECT id, payload, attempts FROM pending ORDER BY id").fetchall()
    if not rows:
        conn.close()
        return

    print(f"[OFFLINE] Trying to flush {len(rows)} pending message(s)...")
    success_ids = []

    for row_id, payload_str, attempts in rows:
        payload = json.loads(payload_str)
        ok = False

        if mqtt_client:
            ok = publish_mqtt(mqtt_client, payload)

        if not ok and use_http:
            ok = send_http(payload)

        if ok:
            success_ids.append(row_id)
        else:
            conn.execute("UPDATE pending SET attempts = attempts + 1 WHERE id = ?", (row_id,))

    for row_id in success_ids:
        conn.execute("DELETE FROM pending WHERE id = ?", (row_id,))

    conn.commit()
    conn.close()
    print(f"[OFFLINE] Flushed {len(success_ids)} / {len(rows)}")


# ===================== MQTT =====================
def create_mqtt_client():
    try:
        import paho.mqtt.client as mqtt
        # Compatible avec les nouvelles versions de paho-mqtt
        try:
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"edge-{RASPI_ID}-{int(time.time())}",
            )
        except AttributeError:
            client = mqtt.Client(client_id=f"edge-{RASPI_ID}-{int(time.time())}")
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
        print(f"[MQTT] Connected to {MQTT_BROKER}:{MQTT_PORT}")
        return client
    except Exception as e:
        print(f"[MQTT] Cannot connect: {e}")
        return None


def publish_mqtt(client, payload: dict) -> bool:
    if client is None:
        return False
    try:
        machine_id = payload.get("machine_id", MACHINE_ID_SIM)
        topic = f"{MQTT_TOPIC_PREFIX}/{machine_id}"
        msg = json.dumps(payload)
        result = client.publish(topic, msg, qos=1)
        if result.rc == 0:
            # ASCII '->' : la flèche U+2192 planterait (UnicodeEncodeError) sur une
            # sortie non UTF-8 et ferait retourner False malgré un envoi réussi,
            # déclenchant le fallback HTTP -> double ingestion de la mesure.
            print(f"[MQTT] Published -> {topic}")
            return True
        print(f"[MQTT] Publish failed rc={result.rc}")
        return False
    except Exception as e:
        print(f"[MQTT] Error: {e}")
        return False


# ===================== HTTP fallback =====================
def send_http(payload: dict) -> bool:
    try:
        import requests
        r = requests.post(DJANGO_PUSH_URL, json=payload, headers=DEVICE_HEADERS, timeout=8)
        print(f"[HTTP] Status {r.status_code}")
        return r.status_code in (200, 201)
    except Exception as e:
        print(f"[HTTP] Failed: {e}")
        return False


def send_heartbeat() -> str | None:
    try:
        import requests
        r = requests.post(HEARTBEAT_URL, json={"raspi_id": RASPI_ID}, headers=DEVICE_HEADERS, timeout=5)
        if r.status_code == 200:
            data = r.json()
            return data.get("machine_id")
    except Exception:
        pass
    return None


# ===================== DATA GENERATION =====================
def generate_simulated_reading(machine_id: str) -> dict:
    """Realistic fake values for testing without hardware."""
    return {
        "machine_id": machine_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "Qb": round(random.uniform(220, 350), 1),
        "PA": round(random.uniform(-180, -60), 1),
        "PTM": round(random.uniform(40, 120), 1),
        "PV": round(random.uniform(80, 220), 1),
        "UF_volume": round(random.uniform(100, 2500), 1),
        "UF_rate": round(random.uniform(200, 800), 1),
        "Heparin": round(random.uniform(500, 1500), 1),
        "_source": "simulation",
        "raspi_id": RASPI_ID,
    }


def analyze_image_real(image_path: str) -> dict | None:
    """Call the existing local AI (server.py) – same as producer.py."""
    try:
        import requests
        with open(image_path, "rb") as f:
            r = requests.post(LOCAL_AI_API, files={"file": f}, timeout=60)
        if r.status_code == 200:
            return r.json()
        print(f"[AI] Error {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[AI] Failed: {e}")
    return None


# ===================== MAIN LOOP =====================
def run(simulation: bool = True, mqtt_only: bool = False):
    init_offline_db()
    mqtt_client = create_mqtt_client()

    print("=" * 50)
    print(f"  Edge Client started")
    print(f"  Mode        : {'SIMULATION' if simulation else 'REAL HARDWARE'}")
    print(f"  MQTT only   : {mqtt_only}")
    if not DEVICE_TOKEN:
        print("  [AUTH] WARNING: DEVICE_TOKEN not set — Django will reject HTTP calls.")
    print(f"  Raspi ID    : {RASPI_ID}")
    print("=" * 50)

    while True:
        # 1. Determine machine_id
        if simulation:
            machine_id = MACHINE_ID_SIM
        else:
            machine_id = send_heartbeat()
            if not machine_id:
                print("[HEARTBEAT] No machine assigned – wait 20s")
                time.sleep(20)
                continue

        # 2. Get data
        if simulation:
            payload = generate_simulated_reading(machine_id)
            print(f"[SIM] Generated -> Qb={payload['Qb']} PA={payload['PA']} PTM={payload['PTM']}")
        else:
            ai_data = analyze_image_real(IMAGE_PATH)
            if not ai_data or "error" in ai_data:
                print("[AI] No valid data – skip this cycle")
                time.sleep(DEFAULT_INTERVAL)
                continue
            payload = {"machine_id": machine_id, **ai_data, "raspi_id": RASPI_ID}

        # 3. Try MQTT first
        sent = publish_mqtt(mqtt_client, payload)

        # 4. Fallback HTTP if allowed
        if not sent and not mqtt_only:
            sent = send_http(payload)

        # 5. Offline if everything failed
        if not sent:
            save_offline(payload)
        else:
            # Try to flush old offline messages
            flush_offline(mqtt_client, use_http=not mqtt_only)

        # 6. Wait
        print(f"--- next reading in {DEFAULT_INTERVAL}s ---\n")
        time.sleep(DEFAULT_INTERVAL)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Edge client for dialysis monitoring")
    parser.add_argument("--real", action="store_true", help="Use real camera + AI (when hardware is ready)")
    parser.add_argument("--mqtt-only", action="store_true", help="Do not fallback to HTTP")
    args = parser.parse_args()

    run(simulation=not args.real, mqtt_only=args.mqtt_only)