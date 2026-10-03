from django.urls import path
from . import views

app_name = "patients"
urlpatterns = [
    path('', views.patient, name='patient'),
    path("add/", views.add_patient, name="add_patient"),
    path("profile/<int:id>/", views.patient_profile, name="Patient_Profile"),
    path('<uuid:seance_id>/detail/', views.session_detail, name='session_detail'),
    path("seances/<uuid:seance_id>/legacy/", views.session_detail_legacy, name="session_detail_legacy"),
    path("delete/<int:id>/", views.delete_patient, name="delete_patient"),
    path("edit/<str:id>/", views.edit_patient, name="edit_patient"),


]