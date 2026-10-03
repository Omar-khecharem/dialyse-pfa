SEUILS_DEFAUT = {
    "Debit_sang": {
        "min": 150, "max": 400, "crit_min": 100, "crit_max": 450,
        "unite": "mL/min", "label": "Débit sanguin",
        "fields": ("blood_flow_min", "blood_flow_max", "blood_flow_critical_low", "blood_flow_critical_high"),
    },
    "PA": {
        "min": 90, "max": 180, "crit_min": 70, "crit_max": 200,
        "unite": "mmHg", "label": "Pression artérielle",
        "fields": ("arterial_pressure_min", "arterial_pressure_max", "arterial_pressure_critical_low", "arterial_pressure_critical_high"),
    },
    "PV": {
        "min": 50, "max": 250, "crit_min": 30, "crit_max": 280,
        "unite": "mmHg", "label": "Pression veineuse",
        "fields": ("venous_pressure_min", "venous_pressure_max", "venous_pressure_critical_low", "venous_pressure_critical_high"),
    },
    "PTM": {
        "min": -50, "max": 300, "crit_min": -80, "crit_max": 350,
        "unite": "mmHg", "label": "Pression transmembranaire",
        "fields": ("tmp_min", "tmp_max", "tmp_critical_low", "tmp_critical_high"),
    },
    "Taux_UF": {
        "min": 0, "max": 1000, "crit_min": 0, "crit_max": 1200,
        "unite": "mL/h", "label": "Taux UF",
        "fields": ("uf_rate_min", "uf_rate_max", None, "uf_rate_critical_high"),
    },
    "Volume_UF": {
        "min": 0, "max": 4000, "crit_min": 0, "crit_max": 5000,
        "unite": "mL", "label": "Volume UF",
        "fields": ("uf_volume_min", "uf_volume_max", None, "uf_volume_critical_high"),
    },
    "Heparine": {
        "min": 0, "max": 2000, "crit_min": 0, "crit_max": 2500,
        "unite": "UI/h", "label": "Héparine",
        "fields": ("heparin_min", "heparin_max", None, "heparin_critical_high"),
    },
}


def _thresholds_for(mesure):
    seance = getattr(mesure, "seance", None)
    thresholds = {}
    for champ, defaults in SEUILS_DEFAUT.items():
        values = defaults.copy()
        if seance is not None:
            for key, field in zip(("min", "max", "crit_min", "crit_max"), defaults["fields"]):
                if field is not None:
                    configured = getattr(seance, field, None)
                    if configured is not None:
                        values[key] = configured
        thresholds[champ] = values
    return thresholds


def analyser_mesure(mesure):
    """Evaluate one measurement using its session thresholds when available."""
    alertes = []

    for champ, s in _thresholds_for(mesure).items():
        valeur = getattr(mesure, champ, None)
        if valeur is None:
            continue

        niveau = None
        message = None
        action = None
        if s["crit_min"] is not None and valeur < s["crit_min"]:
            niveau = "HIGH"
            message = f"🔴 CRITIQUE — {s['label']} très bas : {valeur} {s['unite']} (seuil critique : {s['crit_min']} {s['unite']})"
            action = f"Vérifier immédiatement la ligne. Alerter le médecin si {s['label']} reste sous {s['crit_min']} {s['unite']}."
        elif s["crit_max"] is not None and valeur > s["crit_max"]:
            niveau = "HIGH"
            message = f"🔴 CRITIQUE — {s['label']} très élevé : {valeur} {s['unite']} (seuil critique : {s['crit_max']} {s['unite']})"
            action = f"Réduire le débit ou ajuster les paramètres. Alerter le médecin si {s['label']} dépasse {s['crit_max']} {s['unite']}."
        elif valeur < s["min"]:
            niveau = "MEDIUM"
            message = f"🟡 ATTENTION — {s['label']} bas : {valeur} {s['unite']} (normal min : {s['min']} {s['unite']})"
            action = f"Surveiller {s['label']}. Vérifier les connexions et paramètres de la machine."
        elif valeur > s["max"]:
            niveau = "MEDIUM"
            message = f"🟡 ATTENTION — {s['label']} élevé : {valeur} {s['unite']} (normal max : {s['max']} {s['unite']})"
            action = f"Surveiller {s['label']}. Envisager un ajustement des paramètres de dialyse."

        if niveau:
            alertes.append({
                "alert_type": s["label"],
                "message": message,
                "danger_level": niveau,
                "recommended_action": action,
            })

    return alertes
