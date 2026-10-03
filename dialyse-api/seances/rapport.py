"""
seances/rapport.py — Génération du rapport de séance (RapportSeance).

Rend le template Django `patients/templates/rapport_seance.html` avec les
données RÉELLES de la séance (patient, machine, mesures pré/post, courbes
LiveMeasurement, alertes) puis persiste un unique RapportSeance par séance
(OneToOne). Appelé à la fin de séance, côté Web (seances.views.post_session_page)
comme côté API (api.views.api_session_end).
"""
import json
import datetime

from django.template.loader import render_to_string

from .models import RapportSeance

RAPPORT_TEMPLATE = "rapport_seance.html"


def generate_rapport(seance):
    """Crée/maj le rapport de `seance` et renvoie le RapportSeance.

    Idempotent : `update_or_create` garantit exactement un rapport par séance.
    """
    patient = seance.patient
    machine = seance.machine

    try:
        pre = seance.pre_measurements
    except Exception:
        pre = None
    try:
        post = seance.post_measurements
    except Exception:
        post = None

    # ── Courbes (mêmes données que patients.views.session_detail_legacy) ──
    readings = seance.readings.order_by("timestamp")
    start_dt = None
    if seance.session_date and seance.start_hour:
        try:
            start_dt = datetime.datetime.combine(seance.session_date, seance.start_hour)
        except (TypeError, ValueError):
            # accepte une chaîne "HH:MM" si l'attribut n'a pas été converti
            try:
                start_dt = datetime.datetime.combine(
                    seance.session_date,
                    datetime.datetime.strptime(str(seance.start_hour), "%H:%M").time(),
                )
            except (TypeError, ValueError):
                start_dt = None

    chart_data = []
    for r in readings:
        elapsed_min = 0
        if start_dt and r.timestamp:
            ts = r.timestamp.replace(tzinfo=None)
            elapsed_min = max(0, int((ts - start_dt).total_seconds() / 60))
        chart_data.append({
            "time":      elapsed_min,
            "qb":        r.Debit_sang,
            "pa":        r.PA,
            "ptm":       r.PTM,
            "pv":        r.PV,
            "uf_rate":   r.Taux_UF,
            "uf_volume": r.Volume_UF,
            "heparin":   r.Heparine,
        })

    # ── Alertes de la séance (modèle Alert / related_name="alerts") ──
    alerts = []
    for a in seance.alerts.order_by("timestamp"):
        alerts.append({
            "message":      a.message,
            "danger_level": a.danger_level,
            "timestamp":    a.timestamp.strftime("%H:%M:%S"),
        })

    nb_alertes   = len(alerts)
    nb_critiques = sum(1 for a in alerts if a["danger_level"] == "HIGH")
    qualite = "difficile" if nb_critiques > 0 or (seance.complications or "").strip() else "normale"

    poids_perdu = None
    if pre is not None and post is not None:
        poids_perdu = round((pre.weight or 0.0) - (post.weight or 0.0), 2)

    contenu_html = render_to_string(RAPPORT_TEMPLATE, {
        "patient":      patient,
        "seance":       seance,
        "machine":      machine,
        "pre":          pre,
        "post":         post,
        "qualite":      qualite,
        "nb_alertes":   nb_alertes,
        "nb_critiques": nb_critiques,
        "chart_data":   json.dumps(chart_data),
        "alerts_json":  json.dumps(alerts),
        "poids_perdu":  poids_perdu,
    })

    nom_fichier = "seance_{}_{}{}.html".format(
        seance.session_date or "sansdate",
        patient.last_name,
        patient.first_name,
    )

    rapport, _ = RapportSeance.objects.update_or_create(
        seance=seance,
        defaults={
            "nom_fichier":   nom_fichier,
            "contenu_html":  contenu_html,
            "qualite_seance": qualite,
        },
    )
    return rapport
