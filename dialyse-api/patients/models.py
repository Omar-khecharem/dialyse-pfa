from django.db import models  # Pas djongo !

class Patient(models.Model):
    enumerated_types_dialyse=[
         ('Hémodialyse', 'Hémodialyse'),
        ('Dialyse péritonéale', 'Dialyse péritonéale'),
        ('Transplantation rénale', 'Transplantation rénale'),
    ]
    enumerated_groupes_sanguins = [
        ('A+', 'A+'),
        ('A-', 'A-'),
        ('B+', 'B+'),
        ('B-', 'B-'),
        ('AB+', 'AB+'),
        ('AB-', 'AB-'),
        ('O+', 'O+'),
        ('O-', 'O-'),
        ]
    id = models.AutoField(primary_key=True)
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    date_of_birth=models.DateField(default=None)
    age = models.IntegerField()
    groupe_sanguin=models.CharField(max_length=3 ,default="", choices=enumerated_groupes_sanguins)
    type_de_dialyse=models.CharField(max_length=(50),choices=enumerated_types_dialyse,default="")
    adresse=models.CharField(max_length=255, default="Tunisie")
    telephone=models.CharField(max_length=15,default="")
    contact_urgence=models.CharField(max_length=15,default="")
    antecedents_medicaux=models.CharField(max_length=255 ,default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'patients'  # Optionnel pour Mongo

    def __str__(self):
        return f"{self.first_name} {self.last_name}"



