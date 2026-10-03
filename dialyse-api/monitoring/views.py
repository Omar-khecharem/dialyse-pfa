from django.shortcuts import render
from django.db.models import Q, Avg
from django.utils import timezone

from datetime import datetime, timedelta

from accounts.models import User, UserActivity
from accounts.decorator import app_login_required, role_required

from machines.models import Machine
from seances.models import Seance
from monitoring.models import Alerte


@app_login_required
@role_required("Admin", redirect_to="accounts:error")
def dashboard(request):
    current_user = request.current_user

    # KPIs (valeurs réelles calculées depuis la base)
    kpi_doctors = User.objects.filter(role__name__in=["Docteur", "Admin"]).count()
    kpi_nurses = User.objects.filter(role__name__iexact="Infirmier").count()
    kpi_machines_total = Machine.objects.count()
    kpi_machines_available = Machine.objects.filter(status__iexact="Prete").count()
    limit = timezone.now() - timedelta(minutes=5)
    kpi_active_users = User.objects.filter(etat=True).count()

    # GET params (on garde day et on ajoute q/role/sort)
    selected_day = (request.GET.get("day") or "").strip()
    q = (request.GET.get("q") or "").strip()
    role_filter = (request.GET.get("role") or "").strip()
    sort = (request.GET.get("sort") or "-login_at").strip()
    status = (request.GET.get("status") or "").strip()   # <-- AJOUT

    allowed_sorts = {"login_at", "-login_at", "username", "-username"}
    if sort not in allowed_sorts:
        sort = "-login_at"

    # Base queryset
    qs = UserActivity.objects.select_related("user", "user__role")

    # Filtre par date (comme avant)
    if selected_day:
        qs = qs.filter(login_at__date=selected_day)

    # Recherche utilisateur (username ou email)
    if q:
        qs = qs.filter(
            Q(user__username__icontains=q) |
            Q(user__email__icontains=q)
        )

    # Filtre rôle (liste Docteur/Infirmier)
    if role_filter in ("Docteur", "Infirmier", "Admin"):
        qs = qs.filter(user__role__name__iexact=role_filter)
    
    
    if sort in ("username", "-username"):
        prefix = "-" if sort.startswith("-") else ""
        qs = qs.order_by(f"{prefix}user__username", "-login_at")
    else:
        qs = qs.order_by(sort)
    
    if status == "ongoing":

        qs = qs.filter(logout_at__isnull=True)

    activity_rows = qs[:50]

    context = {
        "current_user": current_user,
        "kpi_doctors": kpi_doctors,
        "kpi_nurses": kpi_nurses,
        "kpi_machines_total": kpi_machines_total,
        "kpi_machines_available": kpi_machines_available,
        "kpi_active_users": kpi_active_users,
        "activity_rows": activity_rows,
        # pour garder les valeurs dans dashboard.html
        "selected_day": selected_day,
        "q": q,
        "role_filter": role_filter,
        "sort": sort,
        "status":status,
    }
    return render(request, "dashboard.html", context)


@app_login_required
@role_required("Admin", "Docteur", "Infirmier", redirect_to="accounts:error")
def surveillance_view(request):
    current_user = request.current_user
    active_sessions = (
           Seance.objects.filter(status="en cours")
          .select_related("patient", "machine")
    )
    return render(request, "surveillance.html", {"current_user": current_user ,"sessions": active_sessions})

@app_login_required
@role_required("Admin", "Docteur", "Infirmier", redirect_to="accounts:error")
def alerts_history(request):

    current_user = request.current_user

    alerts = (
        Alerte.objects
        .select_related(
            "reading",
            "reading__seance",
            "reading__seance__patient",
            "reading__seance__machine",
        )
        .order_by("-timestamp")
    )

    # monitoring.Alerte stores RED/YELLOW while the templates/Flutter expect
    # HIGH/MEDIUM — normalize so badges and level checks stay consistent.
    for alert in alerts:
        niveau = (alert.niveau or "").upper()
        if niveau == "RED":
            alert.niveau = "HIGH"
        elif niveau == "YELLOW":
            alert.niveau = "MEDIUM"

    return render(
        request,
        "alerts_history.html",
        {
            "current_user": current_user,
            "alerts": alerts,

            "total_alerts": alerts.count(),
            "new_alerts": alerts.filter(status="NEW").count(),
            "ack_alerts": alerts.filter(status="ACK").count(),
            "resolved_alerts": alerts.filter(status="RESOLVED").count(),
        },
    )

@app_login_required
@role_required("Admin", "Docteur", "Infirmier", redirect_to="accounts:error")
def seances_history(request):

    current_user = request.current_user

    seances = Seance.objects.select_related(
        "patient",
        "machine"
    ).prefetch_related(
        "readings__alertes"
    ).all().order_by("-session_date")


    for seance in seances:

        readings = seance.readings.all()

        seance.nb_alertes = sum(
            reading.alertes.count()
            for reading in readings
        )

        seance.avg_pa = readings.aggregate(
            Avg("PA")
        )["PA__avg"]

        seance.avg_qb = readings.aggregate(
            Avg("Debit_sang")
        )["Debit_sang__avg"]

        seance.avg_uf = readings.aggregate(
            Avg("Taux_UF")
        )["Taux_UF__avg"]


        if seance.session_date and seance.start_hour:

            start = datetime.combine(
                seance.session_date,
                seance.start_hour
            )

            seance.start_datetime = start

            seance.end_datetime = (
                start +
                timedelta(hours=seance.duration)
            )


    return render(
    request,
    "seances_history.html",
    {
        "seances": seances,
        "current_user": current_user
    }
)
   
