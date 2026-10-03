from django.utils  import timezone

from django.shortcuts import render, get_object_or_404 ,redirect
import json
from accounts.decorator import app_login_required, role_required, device_token_required
from .models import Machine 
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.http import JsonResponse
from .models import RaspiDevice
from django.views.decorators.csrf import csrf_exempt




@app_login_required
@role_required("Admin", "Infirmier", redirect_to="accounts:error")
def machines(request):
    current_user = request.current_user

    # QuerySet GLOBAL (KPI fixes)
    all_machines_qs = Machine.objects.all()

    # GET params
    search = request.GET.get("search", "").strip()
    status_filter = request.GET.get("status", "").strip()
    salle_filter = request.GET.get("salle", "").strip()

    # QuerySet FILTRÉ (liste seulement)
    machines_qs = Machine.objects.all()

    if search:
        machines_qs = machines_qs.filter(machine_id__icontains=search)

    if status_filter:
        machines_qs = machines_qs.filter(status=status_filter)

    if salle_filter:
        machines_qs = machines_qs.filter(location=salle_filter)

    # KPI GLOBAUX
    total_machines = all_machines_qs.count()
    machines_pretes = all_machines_qs.filter(status='Prete').count()
    machines_non_disponibles = all_machines_qs.filter(status='Maintenance').count()
    machines_en_erreur = all_machines_qs.filter(status='Hors Service').count()
    machines_inactives = all_machines_qs.filter(status='Reserve').count()
    

    salles = Machine.objects.values_list('location', flat=True).distinct()

    context = {
        "current_user": current_user,
        "machines": machines_qs.order_by("machine_id"),

        # KPI fixes
        "total_machines": total_machines,
        "machines_pretes": machines_pretes,
        "machines_non_disponibles": machines_non_disponibles,
        "machines_en_erreur": machines_en_erreur,
        "machines_inactives": machines_inactives,
        "salles": salles,
        "enumerated_status": Machine.enumerated_status,
        "request": request,
    }

    return render(request, 'machines.html', context)


@app_login_required
@role_required("Admin", redirect_to="accounts:error")
def ajout_machine(request):
    if request.method == "POST":
        # récupérer les valeurs du formulaire
        machine_id = request.POST.get("machine_id")
        model = request.POST.get("model")
        location = request.POST.get("location")
        type_id = request.POST.get("type")  # <- récupère l'id du type sélectionné

        if machine_id:

            # créer la machine
            Machine.objects.create(
                machine_id=machine_id,
                model=model,
                location=location,
            )
            messages.success(request, "Machine ajoutée avec succès !")
        return redirect("machines:machines")
    
    return redirect("machines:machines")  # jamais afficher un template séparé

@app_login_required
@role_required("Admin", "Infirmier", "Docteur", redirect_to="accounts:error")
def update_status(request, machine_id):
    if request.method == "POST":
        machine = get_object_or_404(Machine, id=machine_id)
        new_status = request.POST.get("status")
        if new_status in dict(Machine.enumerated_status):
            machine.status = new_status
            machine.save()
            messages.success(request, f"Statut de la machine {machine.machine_id} mis à jour !")
        else:
            messages.error(request, "Statut invalide.")
    return redirect("machines:configurer_machine", machine_id=machine_id)



@app_login_required
@role_required("Admin", "Infirmier", "Docteur", redirect_to="accounts:error")
def configurer_machine(request, machine_id):
    # 1️⃣ Récupération de la machine
    machine = get_object_or_404(Machine, id=machine_id)


    # 6️⃣ Traitement POST
    if request.method == "POST":
        # Mise à jour du statut général
        new_status = request.POST.get("status")
        if new_status in dict(Machine.enumerated_status):
            machine.status = new_status
        raspi_id = request.POST.get("raspi_id")

        if raspi_id:
            raspi = RaspiDevice.objects.get(id=raspi_id)

            # libérer ancien appareil de cette machine
            RaspiDevice.objects.filter(machine=machine).update(machine=None)

            # libérer machine déjà utilisée par ce raspi (sécurité)
            RaspiDevice.objects.filter(id=raspi_id).update(machine=machine)

        else:
        # désassigner
            RaspiDevice.objects.filter(machine=machine).update(machine=None)

        machine.save()
    

        messages.success(request, "Configuration de la machine mise à jour avec succès.")
        return redirect("machines:machines")
    
    machines     = Machine.objects.all().order_by('machine_id')
    current_user = request.current_user
    raspis = RaspiDevice.objects.select_related('machine').order_by('raspi_id')  #  c’est ça qui permet au navbar d’afficher le bon nom
    context = {
        "machine": machine,
        "enumerated_status": Machine.enumerated_status,
        "current_user": current_user,
        "raspis": raspis,
        "machines": machines,
    }

    return render(request, "configurer_machine.html", context)

@app_login_required
def details_machine(request, machine_id):
    machine = get_object_or_404(Machine, id=machine_id)
    current_user = request.current_user  # 🔹 pour que le navbar affiche le bon nom
     # Raspi lié (peut être None si aucun assigné)
    raspi = getattr(machine, 'raspi', None)
    # Stats
    recent_sessions = machine.seances.select_related('patient').order_by('-session_date')[:5]
    average_duration = round(machine.hours / machine.sessions, 1) if machine.sessions else 0

    context = {
        "machine": machine,
        "current_user": current_user,
        "raspi": raspi,
        "average_duration": average_duration,
        "recent_sessions": recent_sessions,
    }
    return render(request, "details_machine.html", context)

@app_login_required
@role_required("Admin", "Infirmier", "Docteur", redirect_to="accounts:error")
@require_http_methods(["GET"])
def raspi_management(request):
    current_user = request.current_user
    devices      = RaspiDevice.objects.select_related('machine').order_by('raspi_id')
    machines     = Machine.objects.all().order_by('machine_id')

    # Machines déjà assignées (pour les griser dans le select)
    assigned_machine_ids = set(
        str(d.machine_id) for d in devices if d.machine_id
    )
    # Stats calculées en Python (évite les requêtes djongo incompatibles)
    total    = len(devices)
    assigned = sum(1 for d in devices if d.machine_id)
    free     = sum(1 for d in devices if not d.machine_id)
    inactive = sum(1 for d in devices if d.last_seen is None or (timezone.now() - d.last_seen).total_seconds() > 14400)

    stats = {
        'total':    total,
        'assigned': assigned,
        'free':     free,
        'inactive': inactive,
    }

    return render(request, 'raspi_management.html', {
        'current_user':          current_user,
        'devices':               devices,
        'machines':              machines,
        'stats':                 stats,
        'assigned_machine_ids':  assigned_machine_ids,
    })

@csrf_exempt
@app_login_required
@role_required("Admin", "Infirmier", "Docteur", redirect_to="accounts:error")
@require_http_methods(["POST"])
def assign_machine(request, raspi_id):
    """Assigner ou désassigner une machine à un Raspi."""
    try:
        data       = json.loads(request.body)
        machine_id = data.get('machine_id')  # None = désassigner
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON invalide'}, status=400)

    device = get_object_or_404(RaspiDevice, id=raspi_id)

    if machine_id:
        try:
            machine = Machine.objects.get(id=machine_id)
        except Machine.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Machine introuvable'}, status=404)

        # Libérer l'ancien Raspi qui avait cette machine
        RaspiDevice.objects.filter(machine=machine).exclude(id=raspi_id).update(machine=None)

        device.machine = machine
    else:
        device.machine = None

    device.save(update_fields=['machine'])
    print("ASSIGN VIEW CALLED")

    return JsonResponse({
        'success':     True,
        'raspi_id':    device.raspi_id,
        'machine':     device.machine.machine_id if device.machine else None,
    })


@app_login_required
@role_required("Admin", redirect_to="accounts:error")
@require_http_methods(["POST"])
def add_raspi(request):
    """Ajouter un nouveau Raspi."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON invalide'}, status=400)

    raspi_id    = data.get('raspi_id', '').strip()
    description = data.get('description', '').strip()

    if not raspi_id:
        return JsonResponse({'success': False, 'error': 'raspi_id requis'}, status=400)

    if RaspiDevice.objects.filter(raspi_id=raspi_id).exists():
        return JsonResponse({'success': False, 'error': 'Ce Raspi existe déjà'}, status=409)

    device = RaspiDevice.objects.create(raspi_id=raspi_id, description=description)
    
    messages.success(request, "Appareil ajouté avec succès !")
    return JsonResponse({'success': True, 'id': str(device.id), 'raspi_id': device.raspi_id})

@csrf_exempt 
@require_http_methods(["POST"])
@device_token_required
def raspi_heartbeat(request):
    """
    Endpoint appelé par chaque Raspi pour signaler qu'il est en ligne.
    Authentification par secret partagé (X-Device-Token / DEVICE_TOKEN),
    puis identification par raspi_id enregistré dans la base.
    """
    try:
        data     = json.loads(request.body)
        raspi_id = data.get('raspi_id')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON invalide'}, status=400)

    device = RaspiDevice.objects.filter(raspi_id=raspi_id).first()
    if not device:
        return JsonResponse({'error': 'Raspi inconnu'}, status=404)

    if not device.is_active:
        return JsonResponse({'error': 'Raspi désactivé'}, status=403)

    device.last_seen = timezone.now()
    device.save(update_fields=['last_seen'])

    # Retourner la machine assignée pour que le Raspi sache où envoyer
    return JsonResponse({
        'machine_id': device.machine.machine_id if device.machine else None,
        'is_active':  device.is_active,
    })