import uuid
from django.db import models
from patients.models import Patient
from machines.models import Machine


class Seance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    enumerated_status_seance = [
        ('planifiée', 'Planifiée'),
        ('en cours', 'En cours'),
        ('terminée', 'Terminée'),
        ('annulée', 'Annulée'),
    ]

    # Intervalles d'envoi d'image en secondes selon l'état du patient
    DEBIT_CHOICES = [
        (20, 'Critique – 1 image / 20s'),
        (30, 'Modéré  – 1 image / 30s'),
        (60, 'Normal  – 1 image / 60s'),
    ]

    patient  = models.ForeignKey(Patient,  on_delete=models.CASCADE,   related_name='seances')
    machine  = models.ForeignKey(Machine,  on_delete=models.SET_NULL,  null=True, blank=True, related_name='seances')
    session_date  = models.DateField(null=True, blank=True)
    start_hour    = models.TimeField(null=True, blank=True)
    duration      = models.IntegerField(default=4)
    notes         = models.TextField(blank=True, null=True)
    status        = models.CharField(max_length=20, choices=enumerated_status_seance, default='planifiée')
    complications = models.TextField(max_length=255, null=True, blank=True)
    # ------------------------------------------------------------------ #
    #  SEUILS MACHINE (transmis au modèle IA pour la génération d'alertes) #
    # ------------------------------------------------------------------ #

    # Débit sanguin (mL/min) — normal : 150–400, critique : <100 ou >450
    blood_flow_min         = models.FloatField(default=150)
    blood_flow_max         = models.FloatField(default=400)
    blood_flow_critical_low  = models.FloatField(default=100)
    blood_flow_critical_high = models.FloatField(default=450)

# Pression artérielle machine (mmHg) — normal : 90–180, critique : <70 ou >200
    arterial_pressure_min         = models.FloatField(default=90)
    arterial_pressure_max         = models.FloatField(default=180)
    arterial_pressure_critical_low  = models.FloatField(default=70)
    arterial_pressure_critical_high = models.FloatField(default=200)

# Pression veineuse (mmHg) — normal : 50–250, critique : <30 ou >280
    venous_pressure_min         = models.FloatField(default=50)
    venous_pressure_max         = models.FloatField(default=250)
    venous_pressure_critical_low  = models.FloatField(default=30)
    venous_pressure_critical_high = models.FloatField(default=280)

# Pression transmembranaire PTM (mmHg) — normal : -50–300, critique : <-80 ou >350
    tmp_min         = models.FloatField(default=-50)
    tmp_max         = models.FloatField(default=300)
    tmp_critical_low  = models.FloatField(default=-80)
    tmp_critical_high = models.FloatField(default=350)

# Taux d'ultrafiltration (mL/h) — normal : 0–1000, critique : >1200
    uf_rate_min         = models.FloatField(default=0)
    uf_rate_max         = models.FloatField(default=1000)
    uf_rate_critical_high = models.FloatField(default=1200)

# Volume d'ultrafiltration (mL) — normal : 0–4000, critique : >5000
    uf_volume_min         = models.FloatField(default=0)
    uf_volume_max         = models.FloatField(default=4000)
    uf_volume_critical_high = models.FloatField(default=5000)

# Héparine (UI/h) — normal : 0–2000, critique : >2500
    heparin_min         = models.FloatField(default=0)
    heparin_max         = models.FloatField(default=2000)
    heparin_critical_high = models.FloatField(default=2500)    

    # ------------------------------------------------------------------ #
    #  DÉBIT D'IMAGE                                                       #
    #  Valeur en secondes : intervalle entre deux images envoyées par le  #
    #  Raspberry Pi.  Configurable à la planification (médecin) ou au     #
    #  lancement (infirmier), et mis à jour en cours de séance si         #
    #  l'état du patient évolue.                                           #
    # ------------------------------------------------------------------ #
    debit = models.IntegerField(
        choices=DEBIT_CHOICES,
        default=60,          # Normal par défaut
        help_text=(
            "Intervalle d'envoi d'image en secondes. "
            "20 = critique, 30 = modéré, 60 = normal."
        ),
    )

    class Meta:
        db_table = 'seances'

    def __str__(self):
        return f"Séance {self.patient} - {self.session_date}"

    # ------------------------------------------------------------------ #
    #  Méthodes utilitaires                                                #
    # ------------------------------------------------------------------ #
    def set_debit_from_etat(self, etat: str) -> None:
        """
        Met à jour le débit selon l'état clinique du patient.
        Appeler cette méthode puis .save() (ou save(update_fields=['debit'])).

        etat : 'critique' | 'modéré' | 'normal'
        """
        mapping = {
            'critique': 20,
            'modéré':   30,
            'normal':   60,
        }
        new_debit = mapping.get(etat.lower())
        if new_debit is None:
            raise ValueError(f"État inconnu : '{etat}'. Valeurs acceptées : {list(mapping)}")
        self.debit = new_debit

    def update_debit_en_cours(self, etat: str) -> bool:
        """
        Met à jour et persiste le débit pendant une séance en cours.
        Retourne True si la valeur a effectivement changé.
        """
        ancien = self.debit
        self.set_debit_from_etat(etat)
        if self.debit != ancien:
            self.save(update_fields=['debit'])
            return True
        return False


# 2. MESURES PRÉ-SÉANCE
class PreSessionMeasurements(models.Model):
    id       = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seance   = models.OneToOneField(Seance, on_delete=models.CASCADE, related_name='pre_measurements')
    blood_pressure = models.CharField(max_length=20, blank=True)
    weight         = models.FloatField(default=0.0)
    heart_rate     = models.IntegerField(default=0)
    temperature    = models.FloatField(default=0.0)
    saturation     = models.FloatField(default=0.0)

    class Meta:
        db_table = 'pre_session_measurements'


# 3. MESURES POST-SÉANCE
class PostSessionMeasurements(models.Model):
    id       = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seance   = models.OneToOneField(Seance, on_delete=models.CASCADE, related_name='post_measurements')
    blood_pressure = models.CharField(max_length=20, blank=True)
    weight         = models.FloatField(default=0.0)
    heart_rate     = models.IntegerField(default=0)
    temperature    = models.FloatField(default=0.0)
    saturation     = models.FloatField(default=0.0)

    class Meta:
        db_table = 'post_session_measurements'


class Alert(models.Model):
    DANGER_LEVELS = [
        ('LOW',    'Faible'),
        ('MEDIUM', 'Modéré'),
        ('HIGH',   'Critique'),
    ]
    STATUS_CHOICES = [
        ('NEW', 'Nouvelle'),
        ('ACK', 'Acquittée'),
        ('RESOLVED', 'Résolue'),
    ]

    seance           = models.ForeignKey(Seance, on_delete=models.CASCADE, related_name="alerts")
    timestamp        = models.DateTimeField(auto_now_add=True)
    alert_type       = models.CharField(max_length=100)
    message          = models.TextField()
    danger_level     = models.CharField(max_length=10, choices=DANGER_LEVELS)
    recommended_action = models.TextField()
    status           = models.CharField(max_length=10, choices=STATUS_CHOICES, default='NEW')

    class Meta:
        db_table = 'alerts'
class RapportSeance(models.Model):
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seance     = models.OneToOneField(
        "Seance",                       # ← adaptez le chemin si nécessaire
        on_delete=models.CASCADE,
        related_name="rapport",
    )
    nom_fichier = models.CharField(max_length=255)   # ex: seance_2026-04-22_BenAliMohamed.html
    contenu_html = models.TextField()                # le HTML complet du rapport
    qualite_seance = models.CharField(
        max_length=20,
        choices=[("normale", "Normale"), ("difficile", "Difficile")],
        default="normale",
    )
    
 
    class Meta:
        
        verbose_name = "Rapport de séance"
        verbose_name_plural = "Rapports de séances"
 
    def __str__(self):
        return self.nom_fichier
