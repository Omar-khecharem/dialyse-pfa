from django.db import models
import uuid
from seances.models import Seance


class VitalReading(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    seance = models.ForeignKey(
        Seance,
        on_delete=models.CASCADE,
        related_name="vital_readings"
    )

    timestamp = models.DateTimeField(auto_now_add=True)

    Debit_sang = models.FloatField(null=True, blank=True)
    Taux_UF = models.FloatField(null=True, blank=True)
    PA = models.FloatField(null=True, blank=True)
    PTM = models.FloatField(null=True, blank=True)
    PV = models.FloatField(null=True, blank=True)
    Volume_UF = models.FloatField(null=True, blank=True)
    Heparine = models.FloatField(null=True, blank=True)


    class Meta:
        db_table = "vital_readings"





class ConversationLog(models.Model):

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    message = models.TextField()

    response = models.TextField(
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )


    class Meta:
        db_table = "conversation_logs"
class LiveMeasurement(models.Model):

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    seance = models.ForeignKey(
        Seance,
        on_delete=models.CASCADE,
        related_name="readings"
    )

    timestamp = models.DateTimeField(
        auto_now_add=True
    )

    Debit_sang = models.FloatField(null=True, blank=True)
    Taux_UF = models.FloatField(null=True, blank=True)
    PA = models.FloatField(null=True, blank=True)
    PTM = models.FloatField(null=True, blank=True)
    PV = models.FloatField(null=True, blank=True)
    Volume_UF = models.FloatField(null=True, blank=True)
    Heparine = models.FloatField(null=True, blank=True)


    class Meta:
        db_table = "live_measurements"
class Alerte(models.Model):

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    reading = models.ForeignKey(
        LiveMeasurement,
        on_delete=models.CASCADE,
        related_name="alertes"
    )

    niveau = models.CharField(
        max_length=20
    )

    message = models.TextField()


    status = models.CharField(
        max_length=10,
        default="NEW"
    )


    timestamp = models.DateTimeField(
        auto_now_add=True
    )