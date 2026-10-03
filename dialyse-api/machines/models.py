
import uuid
from django.db import models

# Create your models here.
    
class Machine(models.Model):

    enumerated_status = [
        ('Prete', 'Prete'),
        ('Reserve', 'Reserve'),
        ('Maintenance', 'Maintenance'),
        ('Hors Service', 'Hors Service'),
    ]
    machine_id = models.CharField(max_length=50, unique=True)
    model = models.CharField(max_length=100, default='')
    manufacturer = models.CharField(max_length=100, default='')
    installation_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=50, choices=enumerated_status, default='Prete')
    location = models.CharField(max_length=100, default='')
    sessions = models.IntegerField(default=0)                      
    hours = models.FloatField(default=0)                           
    

    class Meta:
        db_table = 'machines' 
    
    def __str__(self):
        return f"Machine {self.machine_id} - {self.model}"


class RaspiDevice(models.Model):
    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    raspi_id     = models.CharField(max_length=50, unique=True)  # ex: "RASPI-01"
    description  = models.CharField(max_length=100, blank=True)
    machine      = models.OneToOneField(
        'Machine',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='raspi'
    )
    is_active    = models.BooleanField(default=True)
    last_seen    = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'raspi_devices'

    def __str__(self):
        machine_label = self.machine.machine_id if self.machine else "Non assigné"
        return f"{self.raspi_id} → {machine_label}"