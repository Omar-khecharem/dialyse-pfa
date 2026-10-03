from django.contrib import admin
from .models import Machine, RaspiDevice



@admin.register(Machine)
class MachineAdmin(admin.ModelAdmin):
    list_display = ("machine_id", "model", "status", "location")
    search_fields = ("machine_id", "model", "location")
    list_filter = ("status",)



@admin.register(RaspiDevice)
class RaspiDeviceAdmin(admin.ModelAdmin):
    list_display  = ['raspi_id', 'machine', 'is_active', 'last_seen']  # ← 'machine' et 'last_seen'
    list_filter   = ['is_active', 'machine']                            # ← 'machine' pas 'assigned_machine'
    search_fields = ['raspi_id', 'description', 'machine__machine_id']
    list_editable = ['is_active']