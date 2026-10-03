from django.urls import path
from . import views


app_name = "monitoring"


urlpatterns = [

    path(
        '',
        views.dashboard,
        name="dashboard"
    ),

    path(
        'surveillance/',
        views.surveillance_view,
        name="surveillance"
    ),

    path(
        'alerts-history/',
        views.alerts_history,
        name="alerts_history"
    ),

    path(
        'seances-history/',
        views.seances_history,
        name="seances_history"
    ),

]