from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils import timezone
from .models import User, UserActivity

@receiver(pre_save, sender=User, dispatch_uid="accounts_track_etat_change")
def track_etat_change(sender, instance, **kwargs):
    if not instance.pk:
        return

    old = User.objects.filter(pk=instance.pk).only("etat").first()
    if not old:
        return

    # Rien à faire si pas de changement
    if old.etat == instance.etat:
        return

    # False -> True : LOGIN
    if (old.etat is False) and (instance.etat is True):
        UserActivity.objects.create(user=instance, login_at=timezone.now())
        return

    # True -> False : LOGOUT (fermer la dernière session ouverte)
    if (old.etat is True) and (instance.etat is False):
        ua = (UserActivity.objects
              .filter(user=instance, logout_at__isnull=True)
              .order_by("-login_at")
              .first())
        if ua:
            ua.logout_at = timezone.now()
            ua.save(update_fields=["logout_at"])
        return 