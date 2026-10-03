import base64
import json
import re
import random

from fastapi import FastAPI, UploadFile, File
from openai import AsyncOpenAI

app = FastAPI()

# ===================== CONFIG =====================
import os

token = os.getenv("HF_TOKEN")

MODEL_NAME = "Qwen/Qwen3.5-35B-A3B:novita"
HF_API_URL = "https://router.huggingface.co/v1"

client = None

if token:
    client = AsyncOpenAI(
        base_url=HF_API_URL,
        api_key=token
    )
else:
    print("[INFO] No HF_TOKEN found - using fallback mode")

# ===================== FALLBACK ALÉATOIRE =====================
def generate_random_values() -> dict:
    """
    Génère des valeurs simulées réalistes pour une machine de dialyse.
    Utilisé uniquement si le modèle IA est indisponible.
    """
    return {
        "Qb":        round(random.uniform(200, 400), 1),   # Débit sanguin ml/min
        "PTM":       round(random.uniform(50, 250), 1),    # Pression transmembranaire mmHg
        "PA":        round(random.uniform(-200, -50), 1),  # Pression artérielle mmHg
        "PV":        round(random.uniform(50, 200), 1),    # Pression veineuse mmHg
        "UF_volume": round(random.uniform(0, 3000), 1),    # Volume ultrafiltré ml
        "UF_rate":   round(random.uniform(0, 1000), 1),    # Débit UF ml/h
        "Heparin":   round(random.uniform(500, 2000), 1),  # Héparine UI/h
        "_source":   "fallback",                            # Indique que c'est simulé
    }

# ===================== TEST CONNECTIVITÉ MODÈLE =====================
async def model_is_available() -> bool:
    if client is None:
        return False

    try:
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
        )
        return bool(response.choices and response.choices[0].message.content)
    except Exception as e:
        print(f"[MODEL CHECK] Indisponible : {e}")
        return False


PROMPT = """
Analyse l'image et extrait les valeurs numériques affichées.

Répond uniquement en JSON :

{
  "Qb": number or null,
  "PTM": number or null,
  "PA": number or null,
  "PV": number or null,
  "UF_volume": number or null,
  "UF_rate": number or null,
  "Heparin": number or null
}
"""

# ===================== ENDPOINT PRINCIPAL =====================
@app.post("/analyze/")
async def analyze(file: UploadFile = File(...)):
    try:
        image_bytes = await file.read()
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        ext = (file.filename or "image.jpg").split(".")[-1].lower()
        media_type = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"

        # ── Vérifier si le modèle est disponible ──
        if not await model_is_available():
            print("[FALLBACK] Modele indisponible -> valeurs aleatoires")
            return generate_random_values()

        # ── Appel normal au modèle ──
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{b64_image}"}
                        },
                        {"type": "text", "text": PROMPT}
                    ]
                }
            ],
            max_tokens=500
        )

        result = response.choices[0].message.content
        print("RAW:", result)

        result = re.sub(r"```(?:json)?|```", "", result).strip()
        match = re.search(r"\{.*\}", result, re.DOTALL)

        if match:
            data = json.loads(match.group())
            data["_source"] = "model"   # Indique que c'est une vraie lecture
            return data

        # JSON introuvable dans la réponse → fallback
        print("[FALLBACK] JSON non trouve dans la reponse -> valeurs aleatoires")
        return generate_random_values()

    except Exception as e:
        print(f"[FALLBACK] Exception : {e} -> valeurs aleatoires")
        return generate_random_values()


# ===================== ENDPOINT DE STATUT =====================
@app.get("/status/")
async def status():
    """
    Permet au Raspi (ou à Django) de savoir si le modèle est opérationnel.
    """
    available = await model_is_available()
    return {
        "model": MODEL_NAME,
        "available": available,
        "mode": "model" if available else "fallback",
    }
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8001
    )