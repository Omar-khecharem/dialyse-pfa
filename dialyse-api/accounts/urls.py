from django.urls import path, include
from . import views
app_name = "accounts"
urlpatterns = [
    path("api/", include("api.urls")),
    
    path("error/", views.error, name="error"),
    path('', views.login_view, name='login_view'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile, name='profile'),
    path('docteurs/', views.docteurs_list, name='docteurs'),
    path('nurses/', views.nurses_list, name='nurses'),
    path("nurses/ajouter/", views.ajout_infirmier, name="ajout_infirmier"),
    path("add-doctor/", views.add_doctor, name="add_doctor"),
    path("doctor/<int:id>/", views.doctor_profile, name="doctor_profile"),
    path("doctor/<int:id>/edit/", views.edit_doctor, name="edit_doctor"),
    path("doctor/<int:id>/delete/", views.delete_doctor, name="delete_doctor"),
    path("nurse/<int:id>/", views.nurse_profile, name="nurse_profile"),
    path("nurse/<int:id>/edit/", views.edit_nurse, name="edit_nurse"),
    path("nurse/<int:id>/delete/", views.delete_nurse, name="delete_nurse"),
    path("password-reset/", views.password_reset_request, name="password_reset_request"),
    path("password-reset/<str:token>/", views.password_reset_confirm, name="password_reset_confirm"),

] 