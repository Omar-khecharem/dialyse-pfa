
from datetime import date
from django.http import HttpResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.db.models import Q
from .models import Patient
from django.contrib import messages
from accounts.decorator import app_login_required, role_required
from seances.models import PostSessionMeasurements, PreSessionMeasurements, RapportSeance, Seance
from django.core.paginator import Paginator
import json
import datetime
from django.contrib import messages


def calculate_age(dob_str):
    if not dob_str:
        return 0

    try:
        dob = date.fromisoformat(dob_str)
    except (TypeError, ValueError):
        return 0

    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


@app_login_required
@role_required("Admin", "Docteur","Infirmier", redirect_to="accounts:error")
def patient(request):
    current_user = request.current_user
    q = request.GET.get("q", "").strip()
    

    qs = Patient.objects.all().order_by("-created_at")
    patients=qs
    if q:
        qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q))
    context = {
        "current_user": current_user,
        "patients": qs,
        "q": q,
        "patients_tot": patients.count(),
        "groupes_sanguins": Patient.enumerated_groupes_sanguins,
        "types_dialyse": Patient.enumerated_types_dialyse,
    }

    return render(request, "patient.html", context)

@app_login_required
@role_required("Admin", "Docteur", "Infirmier", redirect_to="accounts:error")
def add_patient(request):

    if request.method != "POST":
        return redirect("patients:patient")

    FirstName = request.POST.get("FirstName", "").strip()
    LastName = request.POST.get("LastName", "").strip()
    dateOfBirth = request.POST.get("dateOfBirth")

    contact_urgence = request.POST.get("contacturgences", "").strip()
    address = request.POST.get("address", "").strip()
    telephone = request.POST.get("phone", "").strip()
    antecedents_medicaux = request.POST.get("medicalhistory", "").strip()
    groupe_sanguin = request.POST.get("groupSanguin", "A+")

    patient = Patient.objects.create(
        first_name=FirstName,
        last_name=LastName,
        date_of_birth=dateOfBirth,
        age=calculate_age(dateOfBirth),
        groupe_sanguin=groupe_sanguin,
        type_de_dialyse="Hémodialyse",
        adresse=address,
        telephone=telephone,
        contact_urgence=contact_urgence,
        antecedents_medicaux=antecedents_medicaux,
    )

    messages.success(request, "Patient ajouté avec succès !")

    return redirect("patients:patient")
@app_login_required
@role_required("Admin", "Docteur","Infirmier", redirect_to="accounts:error")
def patient_profile(request, id):
    current_user = request.current_user
    try:
        patient = Patient.objects.get(id=id)
    except Patient.DoesNotExist:
        messages.error(request, "Patient non trouvé.")
        return redirect("patients:patient")
    seances_list = Seance.objects.filter(
        patient=patient
    ).select_related(
        'machine',
        'pre_measurements',
        'post_measurements'
    ).order_by('-session_date', '-start_hour')
    # Pagination (optionnel - 10 séances par page)
    paginator = Paginator(seances_list, 10)
    page_number = request.GET.get('page', 1)
    seances = paginator.get_page(page_number)
    context = {
        "current_user": current_user,
        "patient": patient,
        "groupes_sanguins": Patient.enumerated_groupes_sanguins,
        "seances": seances,
    }
    return render(request, "Patient_Profile.html", context)
@app_login_required
@role_required("Admin", "Docteur", "Infirmier", redirect_to="accounts:error")
def edit_patient(request, id):
    try:
        patient = Patient.objects.get(id=id)
    except Patient.DoesNotExist:
        messages.error(request, "Patient non trouvé.")
        return redirect("patients:patient")

    if request.method != "POST":
        return redirect("patients:Patient_Profile", id=id)

    patient.first_name = request.POST.get("FirstName", "").strip()
    patient.last_name = request.POST.get("LastName", "").strip()
    patient.date_of_birth = request.POST.get("dateOfBirth")
    patient.telephone = request.POST.get("phone", "").strip()
    patient.adresse = request.POST.get("address", "").strip()
    patient.contact_urgence = request.POST.get("contacturgences", "").strip()
    patient.antecedents_medicaux = request.POST.get("medicalhistory", "").strip()
    patient.groupe_sanguin = request.POST.get("groupSanguin", "A+")
    patient.age = calculate_age(request.POST.get("dateOfBirth"))
    patient.save()

    messages.success(request, "Profil modifié avec succès !")
    return redirect("patients:Patient_Profile", id=id)


@app_login_required
@role_required("Admin", "Docteur", "Infirmier", redirect_to="accounts:error")
def session_detail_legacy(request, seance_id):
    current_user = request.current_user
    seance = get_object_or_404(
        Seance.objects.select_related('patient', 'machine')
                      .prefetch_related('readings', 'alerts'),
        id=seance_id
    )

    patient = seance.patient
    machine = seance.machine

    # Pré-séance
    pre = None
    try:
        pre = seance.pre_measurements
    except Exception:
        pass

    # Post-séance
    post = None
    try:
        post = seance.post_measurements
    except Exception:
        pass

    # Mesures live triées
    readings = seance.readings.order_by('timestamp')

    # Calcul temps relatif
    start_dt = None
    if seance.start_hour and seance.session_date:
        start_dt = datetime.datetime.combine(seance.session_date, seance.start_hour)

    chart_data = []
    for r in readings:
        elapsed_min = 0
        if start_dt and r.timestamp:
            ts = r.timestamp.replace(tzinfo=None)
            elapsed_min = max(0, int((ts - start_dt).total_seconds() / 60))
        chart_data.append({
            "time":      elapsed_min,
            "qb":        r.Debit_sang,
            "pa":        r.PA,
            "ptm":       r.PTM,
            "pv":        r.PV,
            "uf_rate":   r.Taux_UF,
            "uf_volume": r.Volume_UF,
            "heparin":   r.Heparine,
        })

    # Alertes
    alerts = []
    for a in seance.alerts.order_by('timestamp'):
        alerts.append({
            "message":      a.message,
            "danger_level": a.danger_level,
            "timestamp":    a.timestamp.strftime('%H:%M:%S'),
        })

    context = {
        "current_user": current_user,
        "seance":         seance,
        "patient":        patient,
        "machine":        machine,
        "pre":            pre,
        "post":           post,
        "chart_data":     json.dumps(chart_data),
        "alerts_json":    json.dumps(alerts),
        "last_reading":   readings.last(),
        "readings_count": readings.count(),
    }
    return render(request, 'session_detail.html', context)
@app_login_required
@role_required("Admin", "Infirmier", "Docteur", redirect_to="accounts:error")
def session_detail(request, seance_id):
    current_user = request.current_user
    seance = get_object_or_404(
        Seance.objects.select_related("patient", "machine"), id=seance_id
    )

    # Séance pas encore terminée → page d'attente simple
    if seance.status != "terminée":
        return HttpResponse("""
            <div style="font-family:sans-serif; text-align:center; margin-top:80px; color:#6b7280;">
                <p style="font-size:2rem;">⏳</p>
                <p style="font-size:1.1rem; font-weight:600;">Séance non encore terminée</p>
                <p style="font-size:0.9rem; margin-top:8px;">
                    Le rapport sera disponible ici dès la fin de la séance.
                </p>
                <a href="javascript:history.back()" 
                   style="display:inline-block; margin-top:20px; font-size:0.85rem; color:#4f46e5;">
                    ← Retour
                </a>
            </div>
        """)
    rapport = RapportSeance.objects.filter(seance=seance).first()
    if not rapport:
        return redirect("patients:session_detail_legacy", seance_id=seance_id)

    return render(request, "rapport_viewer.html", {
        "seance":       seance,
        "contenu_html": rapport.contenu_html,
        "current_user": request.current_user,
    })

@app_login_required
@role_required("Admin", "Docteur", "Infirmier", redirect_to="accounts:error")
def delete_patient(request, id):
    if request.method != "POST":
        return redirect("patients:patient")

    try:
        patient = Patient.objects.get(id=id)
    except Patient.DoesNotExist:
        messages.error(request, "Patient non trouvé.")
        return redirect("patients:patient")

    patient.delete()

    messages.success(request, "Patient supprimé avec succès !")
    return redirect("patients:patient")