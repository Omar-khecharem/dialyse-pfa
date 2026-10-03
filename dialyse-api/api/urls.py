from django.urls import path
from . import views

app_name = "api"

urlpatterns = [
    # Auth
    path("login/", views.mobile_login, name="mobile_login"),
    path("logout/", views.mobile_logout, name="mobile_logout"),
    path("password-reset/request/", views.api_password_reset_request, name="api_password_reset_request"),

    # Patients
    path("patients/", views.api_patients, name="api_patients"),
    path("patients/<str:patient_id>/", views.api_patient_detail, name="api_patient_detail"),
    # Machines
    path("machines/", views.api_machines, name="api_machines"),
    path("machines/<int:machine_id>/", views.api_machine_detail, name="api_machine_detail"),

    # Sessions / Seances
    path("sessions/", views.api_sessions, name="api_sessions"),
    path("sessions/<str:session_id>/", views.api_session_detail, name="api_session_detail"),
    path("sessions/<str:session_id>/start/", views.api_session_start, name="api_session_start"),
    path("sessions/<str:session_id>/end/", views.api_session_end, name="api_session_end"),
    path("sessions/<str:session_id>/cancel/", views.api_session_cancel, name="api_session_cancel"),

    # Alerts
    path("alerts/", views.api_alerts, name="api_alerts"),
    path("alerts/<str:alert_id>/ack/", views.api_alert_ack, name="api_alert_ack"),
    path("alerts/<str:alert_id>/resolve/", views.api_alert_resolve, name="api_alert_resolve"),

    # Dashboard
    path("dashboard/", views.api_dashboard, name="api_dashboard"),

    # Profile (current user)
    path("profile/", views.api_profile, name="api_profile"),

    # Staff
    path("doctors/", views.api_doctors, name="api_doctors"),
    path("doctors/<int:doctor_id>/", views.api_doctor_detail, name="api_doctor_detail"),
    path("nurses/", views.api_nurses, name="api_nurses"),
    path("nurses/<int:nurse_id>/", views.api_nurse_detail, name="api_nurse_detail"),

    # Raspberry Pi devices
    path("devices/", views.api_devices, name="api_devices"),
    path("devices/<uuid:raspi_id>/assign/", views.api_device_assign, name="api_device_assign"),

    # Raspberry Pi & Edge Pipeline
    path("seance/debit/", views.api_seance_debit, name="api_seance_debit"),
    path("push/", views.push_measurement, name="push_measurement"),

    # Monitoring
    path("real-monitoring/", views.real_monitoring, name="real_monitoring"),
    path("monitoring/live/", views.api_monitoring_live, name="api_monitoring_live"),
    path("monitoring/", views.api_monitoring, name="api_monitoring"),
]
