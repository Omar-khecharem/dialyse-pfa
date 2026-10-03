from django.contrib import admin

from seances.models import Seance ,PreSessionMeasurements, PostSessionMeasurements

# Register your models here.
admin.site.register(Seance)
admin.site.register(PreSessionMeasurements)
admin.site.register(PostSessionMeasurements)