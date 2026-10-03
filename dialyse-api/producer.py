import os
import requests
import time

# ===================== CONFIG =====================
# All values are configurable via environment variables (see .env.example).
# No credentials or hardware IDs are hardcoded.
DJANGO_BASE_URL = os.environ.get("DJANGO_BASE_URL", "http://127.0.0.1:8000")

LOCAL_AI_API    = os.environ.get("AI_API_URL", "http://127.0.0.1:8001/analyze/")
DJANGO_API      = f"{DJANGO_BASE_URL}/api/push/"
DEBIT_API       = f"{DJANGO_BASE_URL}/api/seance/debit/"
HEARTBEAT_URL   = f"{DJANGO_BASE_URL}/machines/raspi/heartbeat/"

RASPI_ID        = os.environ.get("RASPI_ID", "RASPI-02")
IMAGE_PATH      = os.environ.get("RASPI_IMAGE_PATH", "frame.jpg")
DEFAULT_DEBIT   = int(os.environ.get("RASPI_DEFAULT_DEBIT", "60"))

# Authentification machine → Django (endpoints /api/push/, /api/seance/debit/,
# /machines/raspi/heartbeat/). Même variable que le serveur (voir .env.example).
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "")
DEVICE_HEADERS = {"X-Device-Token": DEVICE_TOKEN}

# Machine assignée — récupérée dynamiquement via heartbeat, pas codée en dur
MACHINE_ID = None


# ===================== HEARTBEAT =====================
def send_heartbeat() -> str | None:
    """
    Signale que ce Raspi est en ligne.
    Retourne le machine_id assigné à ce Raspi, ou None si non assigné.
    """
    try:
        r = requests.post(
            HEARTBEAT_URL,
            json={"raspi_id": RASPI_ID},
            headers=DEVICE_HEADERS,
            timeout=5
        )
        if r.status_code == 200:
            data       = r.json()
            machine_id = data.get("machine_id")
            is_active  = data.get("is_active", True)

            if not is_active:
                print(f"[HEARTBEAT] Raspi désactivé sur le serveur.")
                return None

            if machine_id:
                print(f"[HEARTBEAT] Machine assignée : {machine_id}")
            else:
                print(f"[HEARTBEAT] Aucune machine assignée à ce Raspi.")

            return machine_id
        else:
            print(f"[HEARTBEAT] Réponse inattendue {r.status_code}")
            return None
    except Exception as e:
        print(f"[HEARTBEAT] Erreur réseau : {e}")
        return None


# ===================== RÉCUPÉRATION DU DÉBIT =====================
def get_debit(machine_id: str) -> int:
    try:
        r = requests.get(
            DEBIT_API,
            params={"machine_id": machine_id},
            headers=DEVICE_HEADERS,
            timeout=5
        )
        if r.status_code == 200:
            debit = r.json().get("debit", DEFAULT_DEBIT)
            print(f"[DEBIT] Intervalle reçu : {debit}s")
            return int(debit)
        else:
            print(f"[DEBIT] Réponse inattendue {r.status_code}, fallback {DEFAULT_DEBIT}s")
            return DEFAULT_DEBIT
    except Exception as e:
        print(f"[DEBIT] Erreur réseau : {e}, fallback {DEFAULT_DEBIT}s")
        return DEFAULT_DEBIT


# ===================== ANALYSE IMAGE =====================
def analyze_image(image_path: str):
    try:
        with open(image_path, "rb") as f:
            response = requests.post(LOCAL_AI_API, files={"file": f}, timeout=60)

        print("AI Status:", response.status_code)
        print("AI Raw   :", response.text[:500])

        if response.status_code == 200:
            data = response.json()
            print("Résultat IA:", data)
            return data
        else:
            print("Erreur serveur IA:", response.text)
            return None

    except Exception as e:
        print("Erreur analyse:", e)
        return None


# ===================== ENVOI DJANGO =====================
def send_to_django(values: dict, machine_id: str):
    if not values or "error" in values:
        print("Données invalides, non envoyées.")
        return

    # machine_id injecté dynamiquement — plus codé en dur
    payload = {"machine_id": machine_id, **values}

    try:
        r = requests.post(DJANGO_API, json=payload, headers=DEVICE_HEADERS, timeout=10)
        print("Django Status:", r.status_code)
        print("Django Réponse:", r.text[:300])
        return r.status_code
    except Exception as e:
        print("Erreur Django:", e)
        return None


# ===================== BOUCLE PRINCIPALE =====================
if __name__ == "__main__":
    print(f"Démarrage du client Raspberry Pi [{RASPI_ID}]...")
    if not DEVICE_TOKEN:
        print("[AUTH] AVERTISSEMENT : DEVICE_TOKEN absent — Django refusera les appels HTTP.")

    while True:
        # 1. Heartbeat → récupère la machine assignée dynamiquement
        machine_id = send_heartbeat()

        if not machine_id:
            print("Aucune machine assignée — attente 30s avant de réessayer...\n")
            time.sleep(30)
            continue

        # 2. Récupérer l'intervalle d'envoi configuré pour cette séance
        debit = get_debit(machine_id)

        # 3. Capturer et analyser l'image
        ai_values = analyze_image(IMAGE_PATH)

        # 4. Envoyer les résultats à Django
        if ai_values:
            send_to_django(ai_values, machine_id)

        # 5. Attendre l'intervalle configuré
        print(f"Attente {debit} secondes...\n")
        time.sleep(debit)