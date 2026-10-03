from datetime import timedelta

from django.utils import timezone

from monitoring.alerte import analyser_mesure
from monitoring.models import Alerte
from seances.models import Alert as SeanceAlert


def check_thresholds(reading):
    """Legacy adapter returning the monitoring model's RED/YELLOW levels."""
    return [
        ("RED" if alert["danger_level"] == "HIGH" else "YELLOW", alert["message"])
        for alert in analyser_mesure(reading)
    ]


def persist_measurement_alerts(seance, measurement):
    """Persist both legacy alert projections from one threshold evaluation."""
    cutoff = timezone.now() - timedelta(minutes=5)
    evaluated = analyser_mesure(measurement)
    monitoring_created = 0
    seance_created = 0

    for alert in evaluated:
        niveau = "RED" if alert["danger_level"] == "HIGH" else "YELLOW"
        if not Alerte.objects.filter(
            reading__seance=seance,
            niveau=niveau,
            message=alert["message"],
            timestamp__gte=cutoff,
        ).exists():
            Alerte.objects.create(reading=measurement, niveau=niveau, message=alert["message"])
            monitoring_created += 1

        if not SeanceAlert.objects.filter(
            seance=seance,
            alert_type=alert["alert_type"],
            danger_level=alert["danger_level"],
            timestamp__gte=cutoff,
        ).exists():
            SeanceAlert.objects.create(
                seance=seance,
                alert_type=alert["alert_type"],
                message=alert["message"],
                danger_level=alert["danger_level"],
                recommended_action=alert["recommended_action"],
            )
            seance_created += 1

    return monitoring_created, seance_created