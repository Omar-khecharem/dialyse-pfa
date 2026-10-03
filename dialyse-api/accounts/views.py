from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.db.models import Q
from .models import User , Role , Profile, PasswordResetRequest
from patients.models import Patient
from seances.models import Seance
from .decorator import app_login_required, role_required 
from django.core.files.base import ContentFile
from django.contrib import messages
import base64
import uuid
import secrets
from .reset_utils import make_reset_token , read_reset_token
from django.contrib.auth.hashers import make_password, check_password
from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.shortcuts import render
from accounts.models import UserActivity

# Create your views here.

@app_login_required
def dashboard(request):
    current_user = request.current_user
    activity_rows = UserActivity.objects.all().order_by("-login_at")

    return render(
        request,
        "dashboard.html",
        {
            "current_user": current_user,
            "activity_rows": activity_rows
        }
    )

def login_view(request):
    auth_error = None
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        try:
            user = User.objects.get(username=username)
            if hasattr(user, "is_active") and not user.is_active:
                auth_error = "Ce compte est désactivé. Contactez l'administrateur."
                return render(request, "login.html", {"auth_error": auth_error})
            
            # Vérifie le mot de passe hashé
            if check_password(password, user.password):
                request.session['app_user_id'] = user.id
                user.etat = True
                user.save()
                if user.first_login:
                    return redirect("accounts:profile")
                return redirect("monitoring:surveillance")  # redirige vers le dashboard après connexion réussie
            else:
                auth_error = "Identifiants invalides!"
        except User.DoesNotExist:
            auth_error = "Identifiants invalides!!"
    return render(request, "login.html", {"auth_error": auth_error})



# ====Vue pour la demande de réinitialisation de mot de passe===
RESET_TOKEN_MAX_AGE = 60 * 30  # 30 min
def password_reset_request(request):
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()

        # Message neutre (sécurité): toujours pareil même si email n'existe pas
        success_msg = "Si un compte existe avec cet email, un lien a été envoyé."

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            return render(request, "login.html", {"success_msg": success_msg})

        token = make_reset_token(user.id)
        PasswordResetRequest.objects.create(user=user, token=token)

        reset_link = request.build_absolute_uri(
            reverse("accounts:password_reset_confirm", args=[token])
        )

        send_mail(
            subject="Réinitialisation de mot de passe",
            message=f"Cliquez sur ce lien pour réinitialiser votre mot de passe: {reset_link}",
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[user.email],
            fail_silently=False,
        )

        return render(request, "reset_message.html", {"success_msg": success_msg})

    return render(request, "login.html")


def password_reset_confirm(request, token):
    user_id = read_reset_token(token, max_age_seconds=RESET_TOKEN_MAX_AGE)
    if not user_id:
        return render(request, "password_reset_confirm.html", {"token_invalid": True})

    prr = PasswordResetRequest.objects.filter(token=token).select_related("user").first()
    if not prr or prr.is_used() or prr.user_id != user_id:
        return render(request, "password_reset_confirm.html", {"token_invalid": True})
    if request.method == "POST":
        p1 = request.POST.get("password1", "")
        p2 = request.POST.get("password2", "")

        if p1 != p2 or len(p1) < 8:
            return render(request, "password_reset_confirm.html", {"token": token, "error": "Mot de passe invalide."})

        prr.user.password = make_password(p1)  # hash Django [web:75]
        prr.user.save()

        prr.used_at = timezone.now()
        prr.save()

        return redirect("accounts:login_view")  # ou une page "reset complete"

    return render(request, "password_reset_confirm.html", {"token": token})




def logout_view(request):  # Sans @login_required
    # Pas besoin auth_logout si session custom
    user_id = request.session.get('app_user_id')
    if user_id:
        user = get_object_or_404(User, id=user_id)
        user.etat = False
        user.save()  # <-- déclenche pre_save -> track_etat_change -> logout_at

    request.session.flush()
    return redirect('accounts:login_view')

@app_login_required
def error(request):
    current_user = request.current_user
    return render(request, 'error.html', {"current_user": current_user})

@app_login_required
@role_required("Admin", "Docteur", redirect_to="accounts:error")
def nurses_list(request):
    current_user = request.current_user

    search_query = request.GET.get("search", "").strip()
    status_filter = request.GET.get("status", "").strip().lower()

    nurses_qs = (
        User.objects
        .select_related("role")
        .filter(role__name__iexact="Infirmier")
        .order_by("username")
    )
    total_nurses=nurses_qs
    kpi_active_nurses = nurses_qs.filter(etat=True).count()

    # ===== Recherche =====
    if search_query:
        nurses_qs = nurses_qs.filter(
            Q(username__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(phone_number__icontains=search_query)
        )

    # ===== Filtre statut =====
    if status_filter == "active":
        nurses_qs = nurses_qs.filter(etat=True)
    elif status_filter == "inactive":
        nurses_qs = nurses_qs.filter(etat=False)

    nurses = []

    for n in nurses_qs:
        join_date = n.date_inscription

        seniority_label = ""
        if join_date:
            now = timezone.now().date()
            years = now.year - join_date.year
            months = now.month - join_date.month
            if months < 0:
                years -= 1
                months += 12

            seniority_label = (
                f"{months} mois"
                if years <= 0
                else f"{years} an" + ("s" if years > 1 else "")
            )

        etat = "Actif" if n.etat else "Inactif"

        assigned_mgr = getattr(n, "assigned_doctors", None)
        doctor_names = [d.username for d in assigned_mgr.all()] if assigned_mgr else []

        nurses.append({
            "id": n.id,
            "firstName": n.username,
            "lastName": "",
            "email": n.email,
            "phone": n.phone_number,
            "assignedDoctorsText": ", ".join(doctor_names) if doctor_names else "Aucun",
            "patientsCount": 0,
            "activeSessions": 0,
            "scheduledSessions": 0,
            "seniorityLabel": seniority_label,
            "status_label": etat,
            "status_color": "bg-green-100 text-green-800" if n.etat else "bg-red-100 text-red-800",
        })
    # KPIs globaux (valeurs réelles calculées depuis la base)
    kpi_total_patients = Patient.objects.count()
    kpi_active_sessions = Seance.objects.filter(status="en cours").count()
    kpi_scheduled_sessions = Seance.objects.filter(status="planifiée").count()
    nurse_count = nurses_qs.count()
    kpi_avg_load = round(kpi_total_patients / nurse_count) if nurse_count else 0
    context = {
        "nurses": nurses,
        "kpi_total_patients": kpi_total_patients,
        "kpi_active_sessions": kpi_active_sessions,
        "kpi_scheduled_sessions": kpi_scheduled_sessions,
        "kpi_avg_load": kpi_avg_load,
        "current_user": current_user,
        "kpi_active_nurses": kpi_active_nurses,
        "total_nurses": total_nurses.count(),
    }

    return render(request, "nurses.html", context)

@app_login_required
@role_required("Admin",  redirect_to="accounts:error")
def docteurs_list(request):
    current_user = request.current_user

    # 1) récupérer les paramètres GET
    search = request.GET.get("search", "").strip()
    role = request.GET.get("role", "").strip()
    status = request.GET.get("status", "").strip()
    


    # 2) Base QuerySet
    doctors_qs = User.objects.select_related("role").all()
    profile_qs = Profile.objects.all()


    # 3) filtrer uniquement les docteurs/admin
    doctors_qs = doctors_qs.filter(role__name__in=["Docteur", "Admin"])
    total_doctors=doctors_qs
    isadmin_count = doctors_qs.filter(role__name="Admin").count()
    isActif_count = doctors_qs.filter(etat=True).count()

    # 4) filtrer par search
    if search:
        doctors_qs = doctors_qs.filter(
            username__icontains=search
        )

    # 5) filtrer par role
    if role == "admin":
        doctors_qs = doctors_qs.filter(role__name="Admin")
    elif role == "doctor":
        doctors_qs = doctors_qs.filter(role__name="Docteur")

    # 6) filtrer par status
    if status == "active":
        doctors_qs = doctors_qs.filter(etat=True)
    elif status == "inactive":
        doctors_qs = doctors_qs.filter(etat=False)

    doctors_qs = doctors_qs.order_by("username")
   
    # 7) préparation du context (comme avant)
    doctors = []
    for d in doctors_qs:
        join_date = d.date_inscription
        profile = profile_qs.filter(user=d).first()
        experience_years=profile.experience if profile and profile.experience else 0
       
        member_since = ""
        etat = "Actif" if d.etat else "Inactif"

        if join_date:
            
            member_since = join_date.strftime("%B %Y")

        speciality = d.specialite or "Généraliste"

        doctors.append({
            "id": d.id,
            "fullName": f"Dr.{d.username}",
            "speciality": speciality,
            "roleLabel": d.role.name,
            "rating": 0,  # aucune donnée de notation en base
            "patientsCount": getattr(d, "patients_count", 0),
            "sessionsCount": 0,  # aucune relation médecin->séance dans le schéma
            "experienceYears": experience_years,
            "email": d.email,
            "phone": d.phone_number,
            "memberSince": member_since,
            "status_label": etat,
            "status_color": "bg-green-100 text-green-800" if d.etat else "bg-red-100 text-red-800",
            
        })

    context = {
        "doctors": doctors,
        "current_user": current_user,
        "total_doctors": total_doctors.count(),
        "isadmin_count": isadmin_count,
        "isActif_count": isActif_count,
    }

    return render(request, "docteurs.html", context)



@app_login_required
@role_required("Admin", "Docteur", redirect_to="accounts:error")
def ajout_infirmier(request):
    if request.method != "POST":
        return redirect("accounts:nurses")

    nom = (request.POST.get("nom") or "").strip()
    email = (request.POST.get("email") or "").strip().lower()
    telephone = (request.POST.get("telephone") or "").strip()

    if not nom or not email:
        messages.error(request, "Nom et email sont obligatoires.")
        return redirect("accounts:nurses")
    # Mot de passe automatique (8 caractères)
    password = secrets.token_urlsafe(8)[:10]

    if User.objects.filter(email=email).exists():
        messages.error(request, "Cet email existe déjà.")
        return redirect("accounts:nurses")

    if User.objects.filter(username=nom).exists():
        messages.error(request, "Ce nom d'utilisateur existe déjà.")
        return redirect("accounts:nurses")

    role_infirmier, _ = Role.objects.get_or_create(name="Infirmier")
    user = User(username=nom, email=email, phone_number=telephone, etat=False,password=make_password(password), role=role_infirmier)
    user.save()

    messages.success(request, f"Infirmier ajouté. Mot de passe : {password}")
    return redirect("accounts:nurses")

@app_login_required
@role_required("Admin", redirect_to="accounts:error")
def add_doctor(request):
    if request.method == "POST":
        fullName = request.POST.get("fullName").strip()
        email = request.POST.get("email").strip().lower()
        speciality = request.POST.get("speciality")
        phone = request.POST.get("phone")

        # Vérifier les champs obligatoires
        if not fullName or not email:
            messages.error(request, "Nom et email sont obligatoires.")
            return redirect("accounts:docteurs")

        # Vérifier l'unicité de l'email
        if User.objects.filter(email=email).exists():
            messages.error(request, "Cet email existe déjà.")
            return redirect("accounts:docteurs")

        # Vérifier l'unicité du nom d'utilisateur
        if User.objects.filter(username=fullName).exists():
            messages.error(request, "Ce nom d'utilisateur existe déjà.")
            return redirect("accounts:docteurs")

        # Mot de passe automatique (8 caractères)
        password = secrets.token_urlsafe(8)[:10]

        # Récupérer le rôle Docteur
        role_doctor, _ = Role.objects.get_or_create(name="Docteur")

        # Créer le User (mot de passe en clair pour l'instant)
        user = User.objects.create(
            username=fullName,
            email=email,
            specialite=speciality,
            phone_number=phone,
            role=role_doctor,
            etat=False,
            password=make_password(password) 
        )

        # Créer le profil associé
        Profile.objects.create(user=user)

        messages.success(request, f"Docteur ajouté avec succès ! Mot de passe : {password}")
        return redirect("accounts:docteurs")
    else:
        return redirect("accounts:docteurs")

@app_login_required
def profile(request):
    current_user = request.current_user
    profile, created = Profile.objects.get_or_create(user=current_user)

    if request.method == "POST":

        # ============================
        #  Mot de passe (IMPORTANT)
        # ============================
        old_password = request.POST.get("old_password", "")
        new_password = request.POST.get("password", "")

        # Si first_login => on n'accepte pas d'enregistrer sans changer le mot de passe
        if current_user.first_login:
            if not old_password or not new_password:
                messages.error(request, "⚠️ Vous devez changer votre mot de passe pour pouvoir enregistrer.")
                return redirect("accounts:profile")

            # vérifier l'ancien mot de passe
            if not check_password(old_password, current_user.password):
                messages.error(request, "Ancien mot de passe incorrect.")
                return redirect("accounts:profile")

            # enregistrer le nouveau mot de passe
            current_user.password = make_password(new_password)
            current_user.first_login = False
            current_user.save()

            messages.success(request, "Mot de passe mis à jour avec succès !")
            return redirect("accounts:profile")

        # ============================
        # Sinon : profil normal
        # ============================

        # (1) IMAGE CROPPÉE / UPLOAD
        cropped_data = request.POST.get("cropped_image")
        if cropped_data:
            format, imgstr = cropped_data.split(';base64,')
            ext = format.split('/')[-1]
            file_name = f"profile_{uuid.uuid4()}.{ext}"
            image_data = ContentFile(base64.b64decode(imgstr), name=file_name)
            profile.image = image_data
        elif request.FILES.get("image"):
            profile.image = request.FILES.get("image")

        # (2) Bio / Formation / Experience
        profile.bio = request.POST.get("bio", "")
        profile.formation = request.POST.get("formation", "")
        profile.experience = request.POST.get("experience", "")
        profile.save()

        # (3) Tel
        new_phone = request.POST.get("phone_number", "")
        if new_phone and new_phone != current_user.phone_number:
            current_user.phone_number = new_phone
            current_user.save()

        # (4) Adresse
        new_adresse = request.POST.get("adress", "")
        if new_adresse and new_adresse != current_user.adress:
            current_user.adress = new_adresse
            current_user.save()

        # (5) Email
        new_email = request.POST.get("email", "")
        if new_email and new_email != current_user.email:
            current_user.email = new_email
            current_user.save()

        # Mot de passe (optionnel si pas first_login)
        if new_password:
            if not check_password(old_password, current_user.password):
                messages.error(request, "Ancien mot de passe incorrect.")
                return redirect("accounts:profile")

            current_user.password = make_password(new_password)
            current_user.save()

        messages.success(request, "Profil mis à jour avec succès !")
        return redirect("accounts:profile")

    return render(request, "profile.html", {
        "user": current_user,
        "profile": profile,
        "current_user": current_user
    })
@app_login_required
@role_required("Admin",  redirect_to="accounts:error")
def doctor_profile(request, id):
    current_user = request.current_user
    doctors_qs=User.objects.filter(Q(role__name="Docteur")|Q(role__name="Admin"))
    doctor = doctors_qs.filter(id=id).first()
    if not doctor:
        messages.error(request, "Médecin introuvable.")
        return redirect("accounts:docteurs")

    profile, _ = Profile.objects.get_or_create(user=doctor)
    return render(request, "doctor_profile.html", {
        "doctor": doctor,
        "profile": profile,
        "current_user": current_user
    })
@app_login_required
@role_required("Admin", "Docteur", redirect_to="accounts:error")
def nurse_profile(request, id):
    current_user = request.current_user
    nurses_qs=User.objects.filter(role__name="Infirmier")
    nurse = nurses_qs.filter(id=id).first()
    if not nurse:
        messages.error(request, "Infirmier introuvable.")
        return redirect("accounts:nurses")

    profile, _ = Profile.objects.get_or_create(user=nurse)
    return render(request, "nurse_profile.html", {
        "nurse": nurse,
        "profile": profile,
        "current_user": current_user
    })

@app_login_required
@role_required("Admin", redirect_to="accounts:error")
def edit_doctor(request, id):
    current_user = request.current_user
    doctors_qs = User.objects.filter(Q(role__name="Docteur") | Q(role__name="Admin"))
    doctor = doctors_qs.filter(id=id).first()
    if not doctor:
        messages.error(request, "Médecin introuvable.")
        return redirect("accounts:docteurs")

    profile, _ = Profile.objects.get_or_create(user=doctor)

    if request.method == "POST":
        fullName = request.POST.get("fullName", "").strip()
        email = request.POST.get("email", "").strip().lower()
        speciality = request.POST.get("speciality", "").strip()
        phone = request.POST.get("phone", "").strip()
        etat = request.POST.get("etat") == "on"
        role_name = request.POST.get("role", "Docteur")

        # Validation
        if not fullName or not email:
            messages.error(request, "Nom et email sont obligatoires.")
            return redirect("accounts:edit_doctor", id=id)

        # Check email uniqueness (excluding current doctor)
        if User.objects.filter(email=email).exclude(id=id).exists():
            messages.error(request, "Cet email existe déjà.")
            return redirect("accounts:edit_doctor", id=id)

        # Check username uniqueness (excluding current doctor)
        if User.objects.filter(username=fullName).exclude(id=id).exists():
            messages.error(request, "Ce nom d'utilisateur existe déjà.")
            return redirect("accounts:edit_doctor", id=id)

        # Update user
        doctor.username = fullName
        doctor.email = email
        doctor.specialite = speciality
        doctor.phone_number = phone
        doctor.etat = etat

        # Update role if changed
        if role_name in ["Docteur", "Admin"]:
            role_obj, _ = Role.objects.get_or_create(name=role_name)
            doctor.role = role_obj

        doctor.save()

        # Update profile
        profile.bio = request.POST.get("bio", "")
        profile.formation = request.POST.get("formation", "")
        profile.experience = request.POST.get("experience", "")
        profile.save()

        messages.success(request, "Médecin mis à jour avec succès !")
        return redirect("accounts:doctor_profile", id=id)

    return render(request, "edit_doctor.html", {
        "doctor": doctor,
        "profile": profile,
        "current_user": current_user
    })

@app_login_required
@role_required("Admin", redirect_to="accounts:error")
def delete_doctor(request, id):
    current_user = request.current_user
    doctors_qs = User.objects.filter(Q(role__name="Docteur") | Q(role__name="Admin"))
    doctor = doctors_qs.filter(id=id).first()

    if not doctor:
        messages.error(request, "Médecin introuvable.")
        return redirect("accounts:docteurs")

    if request.method == "POST":
        doctor.delete()
        messages.success(request, "Médecin supprimé avec succès !")
        return redirect("accounts:docteurs")

    return render(request, "confirm_delete_doctor.html", {
        "doctor": doctor,
        "current_user": current_user
    })

@app_login_required
@role_required("Admin", redirect_to="accounts:error")
def edit_nurse(request, id):
    current_user = request.current_user
    nurses_qs = User.objects.filter(role__name="Infirmier")
    nurse = nurses_qs.filter(id=id).first()
    if not nurse:
        messages.error(request, "Infirmier introuvable.")
        return redirect("accounts:nurses")

    profile, _ = Profile.objects.get_or_create(user=nurse)

    if request.method == "POST":
        fullName = request.POST.get("fullName", "").strip()
        email = request.POST.get("email", "").strip().lower()
        phone = request.POST.get("phone", "").strip()
        etat = request.POST.get("etat") == "on"

        # Validation
        if not fullName or not email:
            messages.error(request, "Nom et email sont obligatoires.")
            return redirect("accounts:edit_nurse", id=id)

        # Check email uniqueness (excluding current nurse)
        if User.objects.filter(email=email).exclude(id=id).exists():
            messages.error(request, "Cet email existe déjà.")
            return redirect("accounts:edit_nurse", id=id)

        # Check username uniqueness (excluding current nurse)
        if User.objects.filter(username=fullName).exclude(id=id).exists():
            messages.error(request, "Ce nom d'utilisateur existe déjà.")
            return redirect("accounts:edit_nurse", id=id)

        # Update user
        nurse.username = fullName
        nurse.email = email
        nurse.phone_number = phone
        nurse.etat = etat
        nurse.save()

        # Update profile
        profile.bio = request.POST.get("bio", "")
        profile.formation = request.POST.get("formation", "")
        profile.experience = request.POST.get("experience", "")
        profile.save()

        messages.success(request, "Infirmier mis à jour avec succès !")
        return redirect("accounts:nurse_profile", id=id)

    return render(request, "edit_nurse.html", {
        "nurse": nurse,
        "profile": profile,
        "current_user": current_user
    })

@app_login_required
@role_required("Admin", redirect_to="accounts:error")
def delete_nurse(request, id):
    current_user = request.current_user
    nurses_qs = User.objects.filter(role__name="Infirmier")
    nurse = nurses_qs.filter(id=id).first()

    if not nurse:
        messages.error(request, "Infirmier introuvable.")
        return redirect("accounts:nurses")

    if request.method == "POST":
        nurse.delete()
        messages.success(request, "Infirmier supprimé avec succès !")
        return redirect("accounts:nurses")

    return render(request, "confirm_delete_nurse.html", {
        "nurse": nurse,
        "current_user": current_user
    })
