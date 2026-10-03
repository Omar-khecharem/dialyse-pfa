from django.contrib import admin
from .models import Role, User, Profile, UserActivity
from django.contrib.auth.hashers import make_password
# Register your models here.
admin.site.register(Role)
admin.site.register(User)
admin.site.register(Profile)
admin.site.register(UserActivity)

class UserAdmin(admin.ModelAdmin):
    list_display = ("username", "email", "role", "etat", "first_login")
    search_fields = ("username", "email")
    list_filter = ("role", "etat")

    def save_model(self, request, obj, form, change):
        # Si création
        if not change:
            obj.password = make_password(obj.password)

        # Si modification du mot de passe
        if change and "password" in form.changed_data:
            obj.password = make_password(obj.password)

        super().save_model(request, obj, form, change)
