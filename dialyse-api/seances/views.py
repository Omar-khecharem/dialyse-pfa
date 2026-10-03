from datetime import date, timedelta
import json

from django.shortcuts import redirect, render
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404
from django.db.models import Q
from django.contrib import messages
from .models import Seance, PostSessionMeasurements,PreSessionMeasurements
from .rapport import generate_rapport
from machines.models import Machine
from patients.models import Patient
from accounts.decorator import app_login_required, role_required
from django.views.decorators.http import require_POST
from django import forms
from django.utils import timezone
from datetime import timedelta



@app_login_required
@role_required("Admin", "Infirmier", "Docteur", redirect_to="accounts:error")
def planning(request):
    current_user = request.current_user
    
    # Date sélectionnée
    selected_date_str = request.GET.get('date', date.today().isoformat())
    try:
        selected_date = date.fromisoformat(selected_date_str)
    except ValueError:
        selected_date = date.today()
    
    # Créneaux horaires
    time_slots = [
        '08:00', '08:30', '09:00', '09:30', '10:00', '10:30',
        '11:00', '11:30', '12:00', '12:30', '13:00', '13:30',
        '14:00', '14:30', '15:00', '15:30', '16:00', '16:30',
        '17:00', '17:30', '18:00'
    ]
    # Machines prêtes
    stations = Machine.objects.all().order_by('machine_id')
    stations_list = [str(m.machine_id) for m in stations]
    machines_data = list(Machine.objects.filter(machine_id__in=stations_list).values('machine_id', 'status'))

    # Séances du jour
    seances = Seance.objects.filter(
        session_date=selected_date,
        start_hour__in=time_slots
    ).select_related('patient', 'machine').order_by('start_hour')
 

    # Stats
    stats = {
        'planifiée': seances.filter(status='planifiée').count(),
        'en_cours': seances.filter(status='en cours').count(),
        'terminée': seances.filter(status='terminée').count(),
        'available': max(len(stations) - seances.filter(status='en cours').count(), 0),
    }
    # JSON pour JS
    sessions_data = [
        {
            'id': str(s.id),
            'start_hour': s.start_hour.strftime('%H:%M') if s.start_hour else '',
            'duration': str(s.duration),
            'status': s.status,
            'patient__first_name': s.patient.first_name,
            'patient__last_name': s.patient.last_name,
            'machine__machine_id': str(s.machine.machine_id) if s.machine else '',
            'machine_status': s.machine.status if s.machine else '',
            'notes': s.notes or '',
        }
        for s in seances
    ]
    
    context = {
        'current_user': current_user,
        'selected_date': selected_date,
        'time_slots': time_slots,
        'seances': seances,
        'stations': stations,
        'stats': stats,
        'patients': Patient.objects.all(),
        'sessions_json': sessions_data,
        'stations_json': stations_list,
        'machines_data': machines_data,
                
    }
    return render(request, 'planning.html', context)


@app_login_required
@role_required("Admin", "Docteur", redirect_to="accounts:error")
@require_http_methods(["GET", "POST"])
def create_session(request):
    current_user = request.current_user
    
    if request.method == 'POST':
        patient_id = request.POST.get('patient')
        start_time = request.POST.get('start_time')
        duration = request.POST.get('duration')
        machine_id = request.POST.get('machine')
        notes = request.POST.get('notes', '')
        session_date_str = request.POST.get('session_date')
        debit_str      = request.POST.get('debit', '60')  

        
        try:
            session_date = date.fromisoformat(session_date_str)
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Date invalide'}, status=400)
        # Validation du débit
        DEBITS_VALIDES = {20, 30, 60}
        try:
            debit = int(debit_str)
            if debit not in DEBITS_VALIDES:
                raise ValueError
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Débit invalide (20, 30 ou 60)'}, status=400)
        
        try:
            patient = Patient.objects.get(id=patient_id)
            machine = Machine.objects.get(id=machine_id)
        except (Patient.DoesNotExist, Machine.DoesNotExist):
            return JsonResponse({'success': False, 'error': 'Patient ou machine introuvable'}, status=404)
        
        seance = Seance.objects.create(
            patient=patient,
            machine=machine,
            session_date=session_date,
            start_hour=start_time,
            duration=int(duration),
            status='planifiée',
            notes=notes,
            debit=debit,
        )
        
        return JsonResponse({'success': True, 'id': str(seance.id)})
    
    # GET
    return redirect("seances:planning")
    
# class PreSessionForm(forms.ModelForm):
#     class Meta:
#         model = PreSessionMeasurements
#         fields = ["weight", "blood_pressure", "temperature", "heart_rate", "saturation"]
#         widgets = {
#             "weight": forms.NumberInput(attrs={"step": "0.1"}),
#             "temperature": forms.NumberInput(attrs={"step": "0.1"}),
#             "saturation": forms.NumberInput(attrs={"step": "0.1"}),
#             "blood_pressure": forms.TextInput(attrs={"placeholder": "120/80"}),
#         }
# Choix exposés au formulaire pré-séance (infirmier)
DEBIT_CHOICES = [
    (20, 'Critique – 1 image / 20s'),
    (30, 'Modéré  – 1 image / 30s'),
    (60, 'Normal  – 1 image / 60s'),
]
class PreSessionLaunchForm(forms.Form):
    """
    Formulaire de lancement affiché à l'infirmier.
    Regroupe les mesures pré-séance ET le débit, en un seul POST.
    """
    # Mesures
    weight         = forms.FloatField(widget=forms.NumberInput(attrs={"step": "0.1"}))
    blood_pressure = forms.CharField(widget=forms.TextInput(attrs={"placeholder": "120/80"}))
    temperature    = forms.FloatField(widget=forms.NumberInput(attrs={"step": "0.1"}))
    heart_rate     = forms.IntegerField()
    saturation     = forms.FloatField(widget=forms.NumberInput(attrs={"step": "0.1"}))

    # Débit — pré-rempli avec la valeur déjà planifiée par le médecin
    debit = forms.ChoiceField(
        choices=DEBIT_CHOICES,
        label="Débit d'image",
        help_text="Intervalle d'envoi des images vers le serveur.",
    )
    # ── Seuils machine — zone normale ──────────────────────────────
    blood_flow_min          = forms.FloatField(initial=150)
    blood_flow_max          = forms.FloatField(initial=400)
    arterial_pressure_min   = forms.FloatField(initial=90)
    arterial_pressure_max   = forms.FloatField(initial=180)
    venous_pressure_min     = forms.FloatField(initial=50)
    venous_pressure_max     = forms.FloatField(initial=250)
    tmp_min                 = forms.FloatField(initial=-50)
    tmp_max                 = forms.FloatField(initial=300)
    uf_rate_min             = forms.FloatField(initial=0)
    uf_rate_max             = forms.FloatField(initial=1000)
    uf_volume_min           = forms.FloatField(initial=0)
    uf_volume_max           = forms.FloatField(initial=4000)
    heparin_min             = forms.FloatField(initial=0)
    heparin_max             = forms.FloatField(initial=2000)

    # ── Seuils machine — zone critique ─────────────────────────────
    blood_flow_critical_low    = forms.FloatField(initial=100)
    blood_flow_critical_high   = forms.FloatField(initial=450)
    arterial_pressure_critical_low  = forms.FloatField(initial=70)
    arterial_pressure_critical_high = forms.FloatField(initial=200)
    venous_pressure_critical_low    = forms.FloatField(initial=30)
    venous_pressure_critical_high   = forms.FloatField(initial=280)
    tmp_critical_low          = forms.FloatField(initial=-80)
    tmp_critical_high         = forms.FloatField(initial=350)
    uf_rate_critical_high     = forms.FloatField(initial=1200)
    uf_volume_critical_high   = forms.FloatField(initial=5000)
    heparin_critical_high     = forms.FloatField(initial=2500)

class PostSessionForm(forms.ModelForm):
    class Meta:
        model = PostSessionMeasurements
        fields = ["weight", "blood_pressure", "temperature", "heart_rate", "saturation"]
        widgets = {
            "weight": forms.NumberInput(attrs={"step": "0.1"}),
            "temperature": forms.NumberInput(attrs={"step": "0.1"}),
            "saturation": forms.NumberInput(attrs={"step": "0.1"}),
            "blood_pressure": forms.TextInput(attrs={"placeholder": "120/80"}),
        }


@app_login_required
@role_required("Admin", "Infirmier", "Docteur", redirect_to="accounts:error")
def pre_session_page(request, session_id):
    current_user = request.current_user
    seance = get_object_or_404(
        Seance.objects.select_related("patient", "machine"), id=session_id
    )

    if seance.status != "planifiée":
        return redirect("seances:planning")

    obj, _ = PreSessionMeasurements.objects.get_or_create(seance=seance)

    if request.method == "POST":
        form = PreSessionLaunchForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data

            if seance.machine and Seance.objects.filter(
                machine=seance.machine,
                status="en cours"
            ).exclude(id=seance.id).exists():
                messages.error(
                    request,
                    "Machine déjà occupée par une séance en cours"
                )
                return redirect("seances:planning")

            obj.weight         = cd["weight"]
            obj.blood_pressure = cd["blood_pressure"]
            obj.temperature    = cd["temperature"]
            obj.heart_rate     = cd["heart_rate"]
            obj.saturation     = cd["saturation"]
            obj.save()

            seuil_fields = [
                "blood_flow_min", "blood_flow_max",
                "blood_flow_critical_low", "blood_flow_critical_high",
                "arterial_pressure_min", "arterial_pressure_max",
                "arterial_pressure_critical_low", "arterial_pressure_critical_high",
                "venous_pressure_min", "venous_pressure_max",
                "venous_pressure_critical_low", "venous_pressure_critical_high",
                "tmp_min", "tmp_max", "tmp_critical_low", "tmp_critical_high",
                "uf_rate_min", "uf_rate_max", "uf_rate_critical_high",
                "uf_volume_min", "uf_volume_max", "uf_volume_critical_high",
                "heparin_min", "heparin_max", "heparin_critical_high",
            ]
            for f in seuil_fields:
                setattr(seance, f, cd[f])

            nouveau_debit = int(cd["debit"])
            fields_to_update = ["status"] + seuil_fields
            if nouveau_debit != seance.debit:
                seance.debit = nouveau_debit
                fields_to_update.append("debit")

            seance.status = "en cours"
            seance.save(update_fields=fields_to_update)

            machine = seance.machine
            machine.sessions += 1
            machine.hours   += seance.duration
            machine.status   = "Reserve"
            machine.save(update_fields=["sessions", "hours", "status"])

            return redirect("seances:planning")

    else:
        form = PreSessionLaunchForm(initial={
            "weight":         obj.weight,
            "blood_pressure": obj.blood_pressure,
            "temperature":    obj.temperature,
            "heart_rate":     obj.heart_rate,
            "saturation":     obj.saturation,
            "debit":          seance.debit,
            "blood_flow_min":                seance.blood_flow_min,
            "blood_flow_max":                seance.blood_flow_max,
            "blood_flow_critical_low":       seance.blood_flow_critical_low,
            "blood_flow_critical_high":      seance.blood_flow_critical_high,
            "arterial_pressure_min":         seance.arterial_pressure_min,
            "arterial_pressure_max":         seance.arterial_pressure_max,
            "arterial_pressure_critical_low":  seance.arterial_pressure_critical_low,
            "arterial_pressure_critical_high": seance.arterial_pressure_critical_high,
            "venous_pressure_min":           seance.venous_pressure_min,
            "venous_pressure_max":           seance.venous_pressure_max,
            "venous_pressure_critical_low":  seance.venous_pressure_critical_low,
            "venous_pressure_critical_high": seance.venous_pressure_critical_high,
            "tmp_min":                       seance.tmp_min,
            "tmp_max":                       seance.tmp_max,
            "tmp_critical_low":              seance.tmp_critical_low,
            "tmp_critical_high":             seance.tmp_critical_high,
            "uf_rate_min":                   seance.uf_rate_min,
            "uf_rate_max":                   seance.uf_rate_max,
            "uf_rate_critical_high":         seance.uf_rate_critical_high,
            "uf_volume_min":                 seance.uf_volume_min,
            "uf_volume_max":                 seance.uf_volume_max,
            "uf_volume_critical_high":       seance.uf_volume_critical_high,
            "heparin_min":                   seance.heparin_min,
            "heparin_max":                   seance.heparin_max,
            "heparin_critical_high":         seance.heparin_critical_high,
        })

    seuils_config = [
        {"name": "blood_flow", "label": "Débit sanguin", "unit": "mL/min", "step": "1",
         "has_crit_low": True, "crit_cols": "2", "full_width": False,
         "val_min": seance.blood_flow_min, "val_max": seance.blood_flow_max,
         "val_crit_low": seance.blood_flow_critical_low, "val_crit_high": seance.blood_flow_critical_high},

        {"name": "arterial_pressure", "label": "Pression artérielle", "unit": "mmHg", "step": "1",
         "has_crit_low": True, "crit_cols": "2", "full_width": False,
         "val_min": seance.arterial_pressure_min, "val_max": seance.arterial_pressure_max,
         "val_crit_low": seance.arterial_pressure_critical_low, "val_crit_high": seance.arterial_pressure_critical_high},

        {"name": "venous_pressure", "label": "Pression veineuse", "unit": "mmHg", "step": "1",
         "has_crit_low": True, "crit_cols": "2", "full_width": False,
         "val_min": seance.venous_pressure_min, "val_max": seance.venous_pressure_max,
         "val_crit_low": seance.venous_pressure_critical_low, "val_crit_high": seance.venous_pressure_critical_high},

        {"name": "tmp", "label": "PTM", "unit": "mmHg", "step": "1",
         "has_crit_low": True, "crit_cols": "2", "full_width": False,
         "val_min": seance.tmp_min, "val_max": seance.tmp_max,
         "val_crit_low": seance.tmp_critical_low, "val_crit_high": seance.tmp_critical_high},

        {"name": "uf_rate", "label": "Taux UF", "unit": "mL/h", "step": "1",
         "has_crit_low": False, "crit_cols": "1", "full_width": False,
         "val_min": seance.uf_rate_min, "val_max": seance.uf_rate_max,
         "val_crit_low": None, "val_crit_high": seance.uf_rate_critical_high},

        {"name": "uf_volume", "label": "Volume UF", "unit": "mL", "step": "1",
         "has_crit_low": False, "crit_cols": "1", "full_width": False,
         "val_min": seance.uf_volume_min, "val_max": seance.uf_volume_max,
         "val_crit_low": None, "val_crit_high": seance.uf_volume_critical_high},

        {"name": "heparin", "label": "Héparine", "unit": "UI/h", "step": "1",
         "has_crit_low": False, "crit_cols": "1", "full_width": True,
         "val_min": seance.heparin_min, "val_max": seance.heparin_max,
         "val_crit_low": None, "val_crit_high": seance.heparin_critical_high},
    ]

    return render(request, "pre_session.html", {
        "seance":       seance,
        "form":         form,
        "current_user": current_user,
        "seuils_config": seuils_config,
    })


@app_login_required
@role_required("Admin", "Infirmier", "Docteur", redirect_to="accounts:error")
def post_session_page(request, session_id):
    current_user = request.current_user
    seance = get_object_or_404(Seance.objects.select_related("patient", "machine"), id=session_id)

    if seance.status != "en cours":
        return redirect("seances:planning")

    obj, _ = PostSessionMeasurements.objects.get_or_create(seance=seance)

    if request.method == "POST":
        form = PostSessionForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            seance.complications = request.POST.get("complications", "")
            seance.status = "terminée"
            seance.save(update_fields=["status", "complications"])
            machine=seance.machine
            machine.status = "Prete"
            machine.save(update_fields=["status"])
            generate_rapport(seance)
            return redirect("seances:planning")
    else:
        form = PostSessionForm(instance=obj)

    complication_options = [
    "Hypotension",
    "Crampes musculaires",
    "Nausées/Vomissements",
    "Céphalées",
    "Douleurs thoraciques",
    "Frissons",
    "Saignement au point de ponction",
    "Autre",
    ]
    return render(request, "post_session.html", {
    "seance": seance,
    "form": form,
    "complication_options": complication_options,
    "current_user":current_user})

@app_login_required
@role_required("Admin", "Docteur", "Infirmier", redirect_to="accounts:error")
@require_POST
def cancel_session(request, session_id):
    seance = get_object_or_404(Seance, id=session_id)

    if seance.status != "planifiée":
        return JsonResponse({"success": False, "error": "Seules les séances planifiées peuvent être annulées."}, status=400)

    seance.status = "annulée"
    seance.save(update_fields=["status"])
    return JsonResponse({"success": True})

from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from datetime import timedelta


def search_sessions(request):
    # Sécurité AJAX
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'error': 'Forbidden'}, status=403)

    # Paramètres
    q      = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    period = request.GET.get('period', 'today').strip()
    date   = request.GET.get('date', '').strip()

    sessions = Seance.objects.select_related('patient', 'machine')

    #  Construction dynamique des filtres
    filters = Q()
    today = timezone.localdate()

    # ──  Recherche patient ─────────────────────────────
    if q:
        filters &= (
            Q(patient__first_name__icontains=q) |
            Q(patient__last_name__icontains=q)
        )

    # ──  Filtre statut ────────────────────────────────
    STATUS_MAP = {
        'planifiée': 'planifiée',
        'en cours': 'en cours',
        'terminée': 'terminée',
        'annulée': 'annulée',
    }

    if status and status != 'all':
        status_db = STATUS_MAP.get(status)
        if status_db:
            filters &= Q(status=status_db)

    # ── 📅 Filtre période ───────────────────────────────
    if period == 'today':
        target = date if date else today
        filters &= Q(session_date=target)

    elif period == 'week':
        start = today - timedelta(days=today.weekday())
        end   = start + timedelta(days=6)
        filters &= Q(session_date__range=(start, end))

    elif period == 'last_week':
        start = today - timedelta(days=today.weekday() + 7)
        end   = start + timedelta(days=6)
        filters &= Q(session_date__range=(start, end))

    elif period == 'month':
        first_day = today.replace(day=1)
        if today.month == 12:
            last_day = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            last_day = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        filters &= Q(session_date__range=(first_day, last_day))

    elif period == 'last_month':
        first_this       = today.replace(day=1)
        last_month_end   = first_this - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        filters &= Q(session_date__range=(last_month_start, last_month_end))

    # period == 'all' → aucun filtre ajouté

    # ── Application des filtres ───────────────────────
    sessions = sessions.filter(filters).order_by('session_date', 'start_hour')

    # ── Sérialisation ────────────────────────────────
    data = list(sessions.values(
        'id',
        'patient__first_name',
        'patient__last_name',
        'machine__machine_id',
        'start_hour',
        'duration',
        'status',
        'notes',
        'session_date'
    )[:50])

    # ── Formatage ───────────────────────────────────
    for s in data:
        s['session_date'] = str(s['session_date']) if s.get('session_date') else ''
        s['start_hour']   = str(s['start_hour'])[:5] if s.get('start_hour') else ''

    return JsonResponse({
        'sessions': data,
        'count': len(data)
    })
