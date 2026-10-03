from django.db import models
from django.core.validators import MinLengthValidator
from django.utils import timezone
from django.contrib.auth.hashers import make_password

# Create your models here.
class Role(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name
class User(models.Model):
    id = models.AutoField(primary_key=True)
    username = models.CharField(max_length=150, unique=True)
    password = models.CharField(max_length=128,validators=[MinLengthValidator(6)], blank=False, null=False)
    email = models.EmailField(unique=True,blank=True, null=True)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    adress = models.CharField(max_length=255, blank=True, null=True)
    phone_number = models.CharField(max_length=20,blank=True, null=True)
    etat=models.BooleanField(default=False,null=True,blank=True)
    date_inscription=models.DateField(auto_now_add=True, null=True)
    specialite = models.CharField(max_length=100, blank=True, null=True)
    first_login = models.BooleanField(default=True)
    last_seen = models.DateTimeField(null=True, blank=True, default=timezone.now)
    is_active = models.BooleanField(default=True)
    def save(self, *args, **kwargs):
        # Si le mot de passe n'est pas déjà hashé, on le hash
        if self.password and not self.password.startswith('pbkdf2_'):
            self.password = make_password(self.password)
        super().save(*args, **kwargs)




    def get_specialite(self):
        if self.role and self.role.name == "Docteur":
            return self.specialite
        return None
    def __str__(self):
        return self.username
    
class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    bio = models.TextField(blank=True, null=True)
    image = models.ImageField(upload_to='photos_profils/', blank=True, null=True)
    formation = models.TextField(blank=True, null=True)
    experience = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.user.username}'s Profile"
    
class PasswordResetRequest(models.Model):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE)
    token = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    def is_used(self):
        return self.used_at is not None
    
class UserActivity(models.Model):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE)
    login_at = models.DateTimeField(default=timezone.now)
    logout_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):

        return f"{self.user_id} {self.login_at} -> {self.logout_at}"