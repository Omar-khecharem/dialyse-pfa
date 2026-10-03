import os

import requests
import random
import time


URL = "http://127.0.0.1:8000/api/push/"

# Authentification machine → Django (même variable que le serveur).
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "")
HEADERS = {"X-Device-Token": DEVICE_TOKEN}


while True:

    data = {
    "machine_id": "M001",
    "Qb": 280,
    "PA": 220,
    "PTM": 70,
    "PV": 200,
    "UF_volume": 500
}


    r = requests.post(
        URL,
        json=data,
        headers=HEADERS
    )


    print(
        "Sent:",
        data,
        "=>",
        r.json()
    )


    time.sleep(3)