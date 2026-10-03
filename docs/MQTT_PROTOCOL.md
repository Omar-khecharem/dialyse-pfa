# MQTT Protocol — PFA-Dialyse

Standard MQTT pour la remontée des mesures des machines de dialyse vers le
serveur Django, utilisé par le Raspberry Pi (`edge_client.py`, `producer.py`)
et le simulateur (`mqtt_simulator.py`).

## Broker

| Paramètre | Valeur par défaut | Variable d'environnement |
|-----------|-------------------|--------------------------|
| Hôte      | `localhost`       | `MQTT_HOST`              |
| Port      | `1883`            | `MQTT_PORT`              |
| QoS       | `1`               | —                        |

## Topic

```
dialysis/machine/{machine_id}
```

- `dialysis/machine/#` : subscription wildcard utilisée par `mqtt_listener.py`.

## Payload (JSON)

Le même payload est produit par le vrai client Edge et par le simulateur.
Les clés correspondent exactement aux champs attendus par
`api.views.push_measurement` / `mqtt_listener.save_measurement` et par le
modèle `monitoring.LiveMeasurement`.

```json
{
  "machine_id": "M001",
  "timestamp":  "2026-08-15T22:00:00+00:00",
  "Qb":         280,
  "UF_rate":    500,
  "PA":         220,
  "PTM":        70,
  "PV":         200,
  "UF_volume":  500,
  "Heparin":    5,
  "_source":    "simulation",
  "raspi_id":   "RASPI-SIM"
}
```

| Clé          | Type   | Description                                        | Champ Django            |
|--------------|--------|----------------------------------------------------|-------------------------|
| `machine_id` | string | Identifiant machine (`Machine.machine_id`)         | —                       |
| `Qb`         | number | Débit sanguin (mL/min)                             | `LiveMeasurement.Debit_sang` |
| `UF_rate`    | number | Débit d'ultrafiltration (mL/h)                     | `LiveMeasurement.Taux_UF` |
| `PA`         | number | Pression artérielle (mmHg)                         | `LiveMeasurement.PA`    |
| `PTM`        | number | Pression transmembranaire (mmHg)                   | `LiveMeasurement.PTM`   |
| `PV`         | number | Pression veineuse (mmHg)                           | `LiveMeasurement.PV`    |
| `UF_volume`  | number | Volume ultrafiltré (mL)                            | `LiveMeasurement.Volume_UF` |
| `Heparin`    | number | Héparine (UI/h)                                    | `LiveMeasurement.Heparine` |
| `timestamp`  | string | ISO-8601 (optionnel)                               | —                       |
| `_source`    | string | `simulation`, `fallback` ou `model` (optionnel)    | —                       |
| `raspi_id`   | string | Identifiant du Raspberry Pi (optionnel)            | —                       |

## Traitement serveur

Le listener (`mqtt_listener.py`) applique la même logique que l'endpoint
HTTP `POST /api/push/` :

1. Machine résolue via `machine_id` ; le message est ignoré si absente.
2. Séance `en cours` requise pour la machine (sinon message ignoré).
3. `LiveMeasurement` créé.
4. Une évaluation unique est réalisée via `analyser_mesure()`, en utilisant
   les seuils de la séance lorsqu'ils sont disponibles, avec les valeurs par
   défaut sinon.
5. Le résultat est persisté dans les deux modèles historiques
   (`monitoring.Alerte` et `seances.Alert`) pour préserver leurs consommateurs
   existants (déduplication sur 5 minutes).

## Endpoints complémentaires (HTTP, même RASPI)

| Rôle          | Endpoint                                   |
|---------------|--------------------------------------------|
| Push HTTP     | `POST {DJANGO_BASE_URL}/api/push/`         |
| Heartbeat     | `POST {DJANGO_BASE_URL}/machines/raspi/heartbeat/` |
| Intervalle    | `GET  {DJANGO_BASE_URL}/api/seance/debit/?machine_id=...` |
| Analyse image | `POST {AI_API_URL}/analyze/`               |

Tous les hôtes/identifiants sont configurables par variables d'environnement
(voir `.env.example`).