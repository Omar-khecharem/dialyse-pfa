from django.urls import path
from . import views
app_name = "machines"
urlpatterns = [
    path('', views.machines, name='machines'),
    path('ajout_machine/', views.ajout_machine, name='ajout_machine'),
    path('configurer/<int:machine_id>/', views.configurer_machine, name='configurer_machine'), 
     path("update_status/<int:machine_id>/", views.update_status, name="update_status"),

    path('details/<int:machine_id>/', views.details_machine, name='details_machine'),  # <- Important
    path('raspi/',                        views.raspi_management, name='raspi-management'),
    path('raspi/add/',                    views.add_raspi,        name='raspi-add'),
    path('raspi/<uuid:raspi_id>/assign/', views.assign_machine,   name='raspi-assign'),
    path('raspi/heartbeat/',              views.raspi_heartbeat,  name='raspi-heartbeat'),

]