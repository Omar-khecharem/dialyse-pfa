"""
api/views.py — PFA-Dialyse Mobile REST API (Phase 1)
"""
import json
import secrets
from datetime import date, datetime, timedelta
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db.models import Avg, Q

from accounts.decorator import device_token_required
from accounts.models import User, UserActivity, Role, Profile, PasswordResetRequest
from accounts.reset_utils import make_reset_token
from machines.models import Machine, RaspiDevice
from monitoring.models import LiveMeasurement, Alerte
from monitoring.services import persist_measurement_alerts
from patients.models import Patient
from seances.models import (
    Seance,
    PreSessionMeasurements,
    PostSessionMeasurements,
    Alert as SeanceAlert,
)
from django.core.mail import send_mail
from django.urls import reverse
from django.conf import settings
from django.core.files.base import ContentFile
import base64
import binascii
import uuid

def _json_ok(data=None, **kwargs):
    body = {"success": True}
    if data is not None:
        body["data"] = data
    body.update(kwargs)
    return JsonResponse(body)

def _json_err(message, status=400):
    return JsonResponse({"success": False, "error": message}, status=status)

def _bind_session_from_header(request):
    """Flutter Web cannot set the Cookie header; load the same Django session
    from X-Session-Id when the browser did not attach sessionid."""
    if request.session.get("app_user_id"):
        return
    session_key = (
        request.headers.get("X-Session-Id")
        or request.META.get("HTTP_X_SESSION_ID")
        or ""
    ).strip()
    if not session_key:
        return
    from importlib import import_module
    from django.conf import settings

    engine = import_module(settings.SESSION_ENGINE)
    store = engine.SessionStore(session_key=session_key)
    if store.exists(session_key):
        request.session = store


def api_login_required(view_func):
    from functools import wraps
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        _bind_session_from_header(request)
        user_id = request.session.get("app_user_id")
        if not user_id:
            return _json_err("Authentication required", status=401)
        try:
            request.current_user = User.objects.select_related("role").get(id=user_id)
        except User.DoesNotExist:
            request.session.flush()
            return _json_err("Session expired", status=401)
        return view_func(request, *args, **kwargs)
    return _wrapped

def _has_role(user, *roles):
    if not hasattr(user, "role") or not user.role:
        return False
    role_name = (user.role.name or "").lower()
    return role_name in {r.lower() for r in roles}

def _patient_pk(patient_or_id):
    """Patient primary keys are persisted as Mongo ObjectId strings (or
    integer AutoField values on SQLite). Mobile clients always receive a
    string and must never int-parse the identifier."""
    if patient_or_id is None:
        return None
    pk = getattr(patient_or_id, "id", patient_or_id)
    return str(pk)

def _patient_dict(p, extra=None):
    data = {
        "id": _patient_pk(p),
        "first_name": p.first_name,
        "last_name": p.last_name,
        "date_of_birth": str(p.date_of_birth) if p.date_of_birth else None,
        "age": p.age,
        "groupe_sanguin": p.groupe_sanguin,
        "type_de_dialyse": p.type_de_dialyse,
        "adresse": p.adresse,
        "telephone": p.telephone,
        "contact_urgence": p.contact_urgence,
        "antecedents_medicaux": p.antecedents_medicaux,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }
    if extra:
        data.update(extra)
    return data

def _seniority_label(join_date):
    if not join_date:
        return ""
    now = timezone.now().date()
    years = now.year - join_date.year
    months = now.month - join_date.month
    if months < 0:
        years -= 1
        months += 12
    if years <= 0:
        return f"{months} mois"
    return f"{years} an" + ("s" if years > 1 else "")

@csrf_exempt
def mobile_login(request):
    if request.method != "POST":
        return _json_err("POST required", status=405)
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _json_err("Invalid JSON body")
    username = body.get("username", "").strip()
    password = body.get("password", "")
    user = User.objects.select_related("role").filter(username=username).first()
    if user is None or not check_password(password, user.password):
        return JsonResponse({"success": False, "message": "Invalid username or password"}, status=401)
    if hasattr(user, "is_active") and not user.is_active:
        return JsonResponse({"success": False, "message": "Account disabled"}, status=403)
    user.etat = True
    user.save(update_fields=["etat"])
    request.session["app_user_id"] = user.id
    request.session.save()
    return JsonResponse({
        "success": True,
        # Same Django session key as Set-Cookie sessionid — Flutter Web
        # stores it and sends X-Session-Id when Cookie cannot be set.
        "sessionid": request.session.session_key,
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role.name if user.role else "",
            "specialite": user.specialite,
            "first_login": user.first_login,
            "phone": user.phone_number,
            "address": user.adress,
        },
    })

@csrf_exempt
@api_login_required
def mobile_logout(request):
    if request.method != "POST":
        return _json_err("POST required", status=405)
    try:
        user = request.current_user
        user.etat = False
        user.save(update_fields=["etat"])
    except Exception:
        pass
    request.session.flush()
    return JsonResponse({"success": True, "message": "Logged out successfully"})


API_RESET_TOKEN_MAX_AGE = 60 * 30  # 30 min, same as accounts.views

@csrf_exempt
def api_password_reset_request(request):
    """Mobile counterpart of accounts.views.password_reset_request.

    Neutral response (security): identical message whether or not the email
    exists, exactly like the web flow."""
    if request.method != "POST":
        return _json_err("POST required", status=405)
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _json_err("Invalid JSON body")
    email = body.get("email", "").strip().lower()
    if not email:
        return _json_err("Email requis")

    success_msg = "Si un compte existe avec cet email, un lien a été envoyé."

    user = User.objects.filter(email__iexact=email).first()
    if not user:
        return _json_ok(message=success_msg)

    token = make_reset_token(user.id)
    PasswordResetRequest.objects.create(user=user, token=token)

    reset_link = request.build_absolute_uri(
        reverse("accounts:password_reset_confirm", args=[token])
    )
    try:
        send_mail(
            subject="Réinitialisation de mot de passe",
            message=(
                f"Cliquez sur ce lien pour réinitialiser votre mot de passe: "
                f"{reset_link}"
            ),
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception:
        # Matching web behavior is not possible here (web lets the exception
        # propagate); keep the neutral answer so enumeration stays impossible.
        pass

    return _json_ok(message=success_msg)

@csrf_exempt
@api_login_required
def api_patients(request):
    if request.method == "GET":
        qs = Patient.objects.all().order_by("last_name", "first_name")

        search = request.GET.get("search", "").strip()

        if search:
            qs = qs.filter(
                Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(telephone__icontains=search)
                | Q(antecedents_medicaux__icontains=search)
            )

        data = [_patient_dict(p) for p in qs]

        return _json_ok(data, count=len(data))

    if request.method == "POST":
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return _json_err("Invalid JSON")

        first_name = str(body.get("first_name", "")).strip()
        last_name = str(body.get("last_name", "")).strip()
        date_of_birth = body.get("date_of_birth")

        if not first_name:
            return _json_err("Le prénom est obligatoire")

        if not last_name:
            return _json_err("Le nom est obligatoire")

        if not date_of_birth:
            return _json_err("La date de naissance est obligatoire")

        try:
            dob = date.fromisoformat(str(date_of_birth))
        except (TypeError, ValueError):
            return _json_err("Date de naissance invalide")

        today = date.today()
        age = today.year - dob.year - (
            (today.month, today.day) < (dob.month, dob.day)
        )

        patient = Patient.objects.create(
            first_name=first_name,
            last_name=last_name,
            date_of_birth=dob,
            age=age,
            groupe_sanguin=str(body.get("groupe_sanguin", "A+")).strip(),
            type_de_dialyse=str(
                body.get("type_de_dialyse", "Hémodialyse")
            ).strip(),
            adresse=str(body.get("adresse", "")).strip(),
            telephone=str(body.get("telephone", "")).strip(),
            contact_urgence=str(
                body.get("contact_urgence", "")
            ).strip(),
            antecedents_medicaux=str(
                body.get("antecedents_medicaux", "")
            ).strip(),
        )

        return _json_ok(
            _patient_dict(patient),
            message="Patient ajouté avec succès",
        )

    return _json_err("GET or POST required", status=405)


@csrf_exempt
@api_login_required
def api_patient_detail(request, patient_id):
    try:
        patient = Patient.objects.get(id=patient_id)
    except (Patient.DoesNotExist, ValueError, TypeError):
        return _json_err("Patient not found", status=404)

    if request.method == "GET":
        sessions = list(
            Seance.objects.filter(patient=patient)
            .select_related("machine")
            .order_by("-session_date")[:10]
            .values(
                "id",
                "session_date",
                "status",
                "duration",
                "machine__machine_id",
            )
        )

        for s in sessions:
            s["id"] = str(s["id"])
            s["session_date"] = (
                str(s["session_date"]) if s["session_date"] else None
            )

        data = _patient_dict(patient, extra={"recent_sessions": sessions})

        return _json_ok(data)

    if request.method == "PUT":
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return _json_err("Invalid JSON")

        first_name = str(
            body.get("first_name", patient.first_name)
        ).strip()

        last_name = str(
            body.get("last_name", patient.last_name)
        ).strip()

        date_of_birth = body.get(
            "date_of_birth",
            str(patient.date_of_birth) if patient.date_of_birth else None,
        )

        if not first_name:
            return _json_err("Le prénom est obligatoire")

        if not last_name:
            return _json_err("Le nom est obligatoire")

        try:
            dob = date.fromisoformat(str(date_of_birth))
        except (TypeError, ValueError):
            return _json_err("Date de naissance invalide")

        today = date.today()
        age = today.year - dob.year - (
            (today.month, today.day) < (dob.month, dob.day)
        )

        patient.first_name = first_name
        patient.last_name = last_name
        patient.date_of_birth = dob
        patient.age = age
        patient.groupe_sanguin = str(
            body.get("groupe_sanguin", patient.groupe_sanguin)
        ).strip()

        patient.type_de_dialyse = str(
            body.get("type_de_dialyse", patient.type_de_dialyse)
        ).strip()

        patient.adresse = str(
            body.get("adresse", patient.adresse)
        ).strip()

        patient.telephone = str(
            body.get("telephone", patient.telephone)
        ).strip()

        patient.contact_urgence = str(
            body.get("contact_urgence", patient.contact_urgence)
        ).strip()

        patient.antecedents_medicaux = str(
            body.get(
                "antecedents_medicaux",
                patient.antecedents_medicaux,
            )
        ).strip()

        patient.save()

        return _json_ok(
            _patient_dict(patient),
            message="Patient modifié avec succès",
        )
    if request.method == "DELETE":
        patient.delete()
        return _json_ok(
            {"id": _patient_pk(patient_id)},
            message="Patient supprimé avec succès",
        )

    return _json_err("GET, PUT or DELETE required", status=405)




def _raspi_info(machine):
    try:
        raspi = getattr(machine, "raspi", None)
        if not raspi:
            return None
        return {
            "raspi_id": raspi.raspi_id,
            "description": raspi.description,
            "is_active": raspi.is_active,
            "last_seen": raspi.last_seen.isoformat() if raspi.last_seen else None,
        }
    except Exception:
        return None

def _machine_dict(m):
    return {
        "id": m.id,
        "machine_id": m.machine_id,
        "model": m.model,
        "manufacturer": m.manufacturer,
        "installation_date": str(m.installation_date) if m.installation_date else None,
        "status": m.status,
        "location": m.location,
        "sessions": m.sessions,
        "hours": m.hours,
        "raspi": _raspi_info(m),
    }

@csrf_exempt
@api_login_required
def api_machines(request):
    if request.method == "POST":
        current_user = request.current_user
        if not _has_role(current_user, "Admin"):
            return _json_err("Insufficient permissions — Admin only", status=403)
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return _json_err("Invalid JSON")
        machine_id = str(body.get("machine_id", "")).strip()
        model = str(body.get("model", "")).strip()
        location = str(body.get("location", "")).strip()
        if not machine_id:
            return _json_err("machine_id is required")
        if Machine.objects.filter(machine_id=machine_id).exists():
            return _json_err("Cette machine existe déjà", status=409)
        m = Machine.objects.create(
            machine_id=machine_id,
            model=model,
            location=location,
        )
        return _json_ok(_machine_dict(m), message="Machine ajoutée avec succès")

    if request.method != "GET":
        return _json_err("GET or POST required", status=405)
    machines = Machine.objects.all().order_by("machine_id")
    search = request.GET.get("search", "").strip()
    status_filter = request.GET.get("status", "").strip()
    salle_filter = request.GET.get("location", "").strip() or request.GET.get("salle", "").strip()
    if search:
        machines = machines.filter(machine_id__icontains=search)
    if status_filter:
        machines = machines.filter(status=status_filter)
    if salle_filter:
        machines = machines.filter(location=salle_filter)
    data = [_machine_dict(m) for m in machines]
    return _json_ok(data, count=len(data))

@csrf_exempt
@api_login_required
def api_machine_detail(request, machine_id):
    try:
        m = Machine.objects.get(id=machine_id)
    except (Machine.DoesNotExist, ValueError, TypeError, ValidationError):
        return _json_err("Machine not found", status=404)

    if request.method == "PUT":
        current_user = request.current_user
        if not _has_role(current_user, "Admin", "Infirmier", "Docteur"):
            return _json_err("Insufficient permissions", status=403)
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return _json_err("Invalid JSON")
        new_status = body.get("status")
        if new_status is not None:
            if new_status not in dict(Machine.enumerated_status):
                return _json_err("Statut invalide")
            m.status = new_status
            m.save(update_fields=["status"])
        if "raspi_id" in body:
            raspi_id = body.get("raspi_id")
            RaspiDevice.objects.filter(machine=m).update(machine=None)
            if raspi_id:
                try:
                    raspi = RaspiDevice.objects.get(id=raspi_id)
                except (RaspiDevice.DoesNotExist, ValueError, TypeError):
                    return _json_err("Raspi introuvable", status=404)
                RaspiDevice.objects.filter(id=raspi.id).update(machine=m)
        return _json_ok(_machine_dict(m), message="Configuration de la machine mise à jour avec succès.")

    if request.method != "GET":
        return _json_err("GET or PUT required", status=405)
    active_seance = (
        Seance.objects.filter(machine=m, status="en cours")
        .select_related("patient")
        .first()
    )
    active_session_data = None
    if active_seance:
        active_session_data = {
            "id": str(active_seance.id),
            "patient": str(active_seance.patient),
            "session_date": str(active_seance.session_date),
            "status": active_seance.status,
        }
    data = {
        "id": m.id,
        "machine_id": m.machine_id,
        "model": m.model,
        "manufacturer": m.manufacturer,
        "installation_date": str(m.installation_date) if m.installation_date else None,
        "status": m.status,
        "location": m.location,
        "sessions": m.sessions,
        "hours": m.hours,
        "raspi": _raspi_info(m),
        "active_session": active_session_data,
    }
    return _json_ok(data)

def _seance_summary(s):
    readings = s.readings.all()
    start_dt = None
    end_dt = None
    if s.session_date and s.start_hour:
        start_dt = datetime.combine(s.session_date, s.start_hour)
        end_dt = start_dt + timedelta(hours=s.duration)
    return {
        "id": str(s.id),
        "patient": {
            "id": _patient_pk(s.patient),
            "first_name": s.patient.first_name,
            "last_name": s.patient.last_name,
        } if s.patient else None,
        "machine": {
            "id": s.machine.id,
            "machine_id": s.machine.machine_id,
            "location": s.machine.location,
            "status": s.machine.status,
        } if s.machine else None,
        "session_date": str(s.session_date) if s.session_date else None,
        "start_hour": str(s.start_hour)[:5] if s.start_hour else None,
        "duration": s.duration,
        "notes": s.notes,
        "status": s.status,
        "complications": s.complications,
        "debit": s.debit,
        "start_datetime": start_dt.isoformat() if start_dt else None,
        "end_datetime": end_dt.isoformat() if end_dt else None,
        "nb_alertes": sum(reading.alertes.count() for reading in readings),
        "avg_pa": readings.aggregate(Avg("PA"))["PA__avg"],
        "avg_qb": readings.aggregate(Avg("Debit_sang"))["Debit_sang__avg"],
        "avg_uf": readings.aggregate(Avg("Taux_UF"))["Taux_UF__avg"],
    }

@csrf_exempt
@api_login_required
def api_sessions(request):
    if request.method == "GET":
        return _api_sessions_list(request)
    if request.method == "POST":
        return _api_sessions_create(request)
    return _json_err("Method not allowed", status=405)

def _api_sessions_list(request):
    qs = Seance.objects.select_related("patient", "machine").order_by("-session_date", "-start_hour")
    status_filter = request.GET.get("status", "").strip()
    if status_filter:
        qs = qs.filter(status=status_filter)
    patient_id = request.GET.get("patient_id", "").strip()
    if patient_id:
        if not patient_id.isdigit():
            return _json_err("Invalid patient_id — must be numeric")
        qs = qs.filter(patient__id=patient_id)
    machine_id = request.GET.get("machine_id", "").strip()
    if machine_id:
        if not machine_id.isdigit():
            return _json_err("Invalid machine_id — must be numeric")
        qs = qs.filter(machine__id=machine_id)
    date_filter = request.GET.get("date", "").strip()
    if date_filter:
        try:
            qs = qs.filter(session_date=date.fromisoformat(date_filter))
        except ValueError:
            return _json_err("Invalid date format — use YYYY-MM-DD")
    date_from = request.GET.get("date_from", "").strip()
    if date_from:
        try:
            qs = qs.filter(session_date__gte=date.fromisoformat(date_from))
        except ValueError:
            return _json_err("Invalid date_from format — use YYYY-MM-DD")
    date_to = request.GET.get("date_to", "").strip()
    if date_to:
        try:
            qs = qs.filter(session_date__lte=date.fromisoformat(date_to))
        except ValueError:
            return _json_err("Invalid date_to format — use YYYY-MM-DD")
    search = request.GET.get("search", "").strip()
    if search:
        qs = qs.filter(Q(patient__first_name__icontains=search) | Q(patient__last_name__icontains=search))
    data = [_seance_summary(s) for s in qs[:100]]
    return _json_ok(data, count=len(data))

def _api_sessions_create(request):
    current_user = request.current_user
    if not _has_role(current_user, "Admin", "Docteur"):
        return _json_err("Insufficient permissions — Doctor or Admin only", status=403)
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return _json_err("Invalid JSON body")
    patient_id = body.get("patient_id")
    machine_db_id = body.get("machine_id")
    session_date_str = body.get("session_date")
    start_time = body.get("start_time")
    duration = body.get("duration", 4)
    notes = body.get("notes", "")
    debit_val = body.get("debit", 60)
    if not all([patient_id, session_date_str]):
        return _json_err("patient_id and session_date are required")
    VALID_DEBITS = {20, 30, 60}
    try:
        debit_val = int(debit_val)
        if debit_val not in VALID_DEBITS:
            raise ValueError
    except (TypeError, ValueError):
        return _json_err("debit must be 20, 30, or 60")
    try:
        session_date_obj = date.fromisoformat(session_date_str)
    except (TypeError, ValueError):
        return _json_err("Invalid session_date — use YYYY-MM-DD")
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        return _json_err("Invalid duration — must be a number")
    try:
        patient = Patient.objects.get(id=patient_id)
    except (Patient.DoesNotExist, ValueError, TypeError):
        return _json_err("Patient not found", status=404)
    machine = None
    if machine_db_id:
        if not str(machine_db_id).isdigit():
            return _json_err("Invalid machine_id — must be numeric", status=404)
        try:
            machine = Machine.objects.get(id=machine_db_id)
        except Machine.DoesNotExist:
            return _json_err("Machine not found", status=404)
    seance = Seance.objects.create(
        patient=patient,
        machine=machine,
        session_date=session_date_obj,
        start_hour=start_time,
        duration=int(duration),
        status="planifiée",
        notes=notes,
        debit=debit_val,
    )
    return JsonResponse({"success": True, "id": str(seance.id)}, status=201)

@api_login_required
def api_session_detail(request, session_id):
    if request.method != "GET":
        return _json_err("GET required", status=405)
    try:
        seance = Seance.objects.select_related("patient", "machine").get(id=session_id)
    except (Seance.DoesNotExist, ValueError, TypeError, ValidationError):
        return _json_err("Session not found", status=404)
    try:
        pre = seance.pre_measurements
        pre_data = {
            "weight": pre.weight,
            "blood_pressure": pre.blood_pressure,
            "heart_rate": pre.heart_rate,
            "temperature": pre.temperature,
            "saturation": pre.saturation,
        }
    except PreSessionMeasurements.DoesNotExist:
        pre_data = None
    try:
        post = seance.post_measurements
        post_data = {
            "weight": post.weight,
            "blood_pressure": post.blood_pressure,
            "heart_rate": post.heart_rate,
            "temperature": post.temperature,
            "saturation": post.saturation,
        }
    except PostSessionMeasurements.DoesNotExist:
        post_data = None
    thresholds = {
        "blood_flow_min": seance.blood_flow_min,
        "blood_flow_max": seance.blood_flow_max,
        "blood_flow_critical_low": seance.blood_flow_critical_low,
        "blood_flow_critical_high": seance.blood_flow_critical_high,
        "arterial_pressure_min": seance.arterial_pressure_min,
        "arterial_pressure_max": seance.arterial_pressure_max,
        "arterial_pressure_critical_low": seance.arterial_pressure_critical_low,
        "arterial_pressure_critical_high": seance.arterial_pressure_critical_high,
        "venous_pressure_min": seance.venous_pressure_min,
        "venous_pressure_max": seance.venous_pressure_max,
        "venous_pressure_critical_low": seance.venous_pressure_critical_low,
        "venous_pressure_critical_high": seance.venous_pressure_critical_high,
        "tmp_min": seance.tmp_min,
        "tmp_max": seance.tmp_max,
        "tmp_critical_low": seance.tmp_critical_low,
        "tmp_critical_high": seance.tmp_critical_high,
        "uf_rate_min": seance.uf_rate_min,
        "uf_rate_max": seance.uf_rate_max,
        "uf_rate_critical_high": seance.uf_rate_critical_high,
        "uf_volume_min": seance.uf_volume_min,
        "uf_volume_max": seance.uf_volume_max,
        "uf_volume_critical_high": seance.uf_volume_critical_high,
        "heparin_min": seance.heparin_min,
        "heparin_max": seance.heparin_max,
        "heparin_critical_high": seance.heparin_critical_high,
        "debit": seance.debit,
    }
    alerts_data = [
        {
            "id": a.id,
            "alert_type": a.alert_type,
            "message": a.message,
            "danger_level": a.danger_level,
            "recommended_action": a.recommended_action,
            "timestamp": a.timestamp.isoformat() if a.timestamp else None,
        }
        for a in SeanceAlert.objects.filter(seance=seance).order_by("-timestamp")
    ]
    readings = seance.readings.order_by("timestamp")
    start_dt = None
    if seance.session_date and seance.start_hour:
        start_dt = datetime.combine(seance.session_date, seance.start_hour)
    chart_data = []
    for r in readings:
        elapsed_min = 0
        if start_dt and r.timestamp:
            ts = r.timestamp.replace(tzinfo=None)
            elapsed_min = max(0, int((ts - start_dt).total_seconds() / 60))
        chart_data.append({
            "time": elapsed_min,
            "qb": r.Debit_sang,
            "pa": r.PA,
            "ptm": r.PTM,
            "pv": r.PV,
            "uf_rate": r.Taux_UF,
            "uf_volume": r.Volume_UF,
            "heparin": r.Heparine,
        })
    last_reading = readings.last()
    last_reading_data = None
    if last_reading is not None:
        last_reading_data = {
            "volume_uf": last_reading.Volume_UF,
            "debit_sang": last_reading.Debit_sang,
        }
    try:
        rapport = seance.rapport
        rapport_data = {
            "qualite_seance": rapport.qualite_seance,
            "nom_fichier": rapport.nom_fichier,
        }
    except Exception:
        rapport_data = None
    data = {
        **_seance_summary(seance),
        "pre_measurements": pre_data,
        "post_measurements": post_data,
        "thresholds": thresholds,
        "alerts": alerts_data,
        "rapport": rapport_data,
        "readings": chart_data,
        "readings_count": readings.count(),
        "last_reading": last_reading_data,
    }
    return _json_ok(data)

@csrf_exempt
@api_login_required
def api_session_start(request, session_id):
    if request.method != "POST":
        return _json_err("POST required", status=405)
    current_user = request.current_user
    if not _has_role(current_user, "Admin", "Infirmier", "Docteur"):
        return _json_err("Insufficient permissions", status=403)
    try:
        seance = Seance.objects.select_related("machine").get(id=session_id)
    except (Seance.DoesNotExist, ValueError, TypeError, ValidationError):
        return _json_err("Session not found", status=404)
    if seance.status != "planifiée":
        return _json_err(f"Session cannot be started — current status: {seance.status}")
    if seance.machine and Seance.objects.filter(
        machine=seance.machine,
        status="en cours"
    ).exclude(id=seance.id).exists():
        return _json_err(
            "Machine déjà occupée par une séance en cours",
            status=409
        )
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        body = {}
    obj, _ = PreSessionMeasurements.objects.get_or_create(seance=seance)
    obj.weight = body.get("weight", obj.weight)
    obj.blood_pressure = body.get("blood_pressure", obj.blood_pressure)
    obj.temperature = body.get("temperature", obj.temperature)
    obj.heart_rate = body.get("heart_rate", obj.heart_rate)
    obj.saturation = body.get("saturation", obj.saturation)
    obj.save()

    # Same seuil fields as seances.views.pre_session_page (Django web form).
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
    fields_to_update = ["status"]
    for field in seuil_fields:
        if field in body and body[field] is not None:
            try:
                setattr(seance, field, float(body[field]))
                fields_to_update.append(field)
            except (TypeError, ValueError):
                pass

    VALID_DEBITS = {20, 30, 60}
    new_debit = body.get("debit")
    if new_debit is not None:
        try:
            new_debit = int(new_debit)
            if new_debit in VALID_DEBITS and new_debit != seance.debit:
                seance.debit = new_debit
                fields_to_update.append("debit")
        except (TypeError, ValueError):
            pass
    seance.status = "en cours"
    seance.save(update_fields=fields_to_update)
    if seance.machine:
        machine = seance.machine
        machine.sessions += 1
        machine.hours += seance.duration
        machine.status = "Reserve"
        machine.save(update_fields=["sessions", "hours", "status"])
    return _json_ok(message="Session started successfully")

@csrf_exempt
@api_login_required
def api_session_end(request, session_id):
    if request.method != "POST":
        return _json_err("POST required", status=405)
    current_user = request.current_user
    if not _has_role(current_user, "Admin", "Infirmier", "Docteur"):
        return _json_err("Insufficient permissions", status=403)
    try:
        seance = Seance.objects.select_related("machine").get(id=session_id)
    except (Seance.DoesNotExist, ValueError, TypeError, ValidationError):
        return _json_err("Session not found", status=404)
    if seance.status != "en cours":
        return _json_err(f"Session cannot be ended — current status: {seance.status}")
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        body = {}
    obj, _ = PostSessionMeasurements.objects.get_or_create(seance=seance)
    obj.weight = body.get("weight", obj.weight)
    obj.blood_pressure = body.get("blood_pressure", obj.blood_pressure)
    obj.temperature = body.get("temperature", obj.temperature)
    obj.heart_rate = body.get("heart_rate", obj.heart_rate)
    obj.saturation = body.get("saturation", obj.saturation)
    obj.save()
    seance.complications = body.get("complications", seance.complications or "")
    seance.status = "terminée"
    seance.save(update_fields=["status", "complications"])
    if seance.machine:
        seance.machine.status = "Prete"
        seance.machine.save(update_fields=["status"])
    from seances.rapport import generate_rapport
    generate_rapport(seance)
    return _json_ok(message="Session ended successfully")

@csrf_exempt
@api_login_required
def api_session_cancel(request, session_id):
    if request.method != "POST":
        return _json_err("POST required", status=405)
    current_user = request.current_user
    if not _has_role(current_user, "Admin", "Infirmier", "Docteur"):
        return _json_err("Insufficient permissions", status=403)
    try:
        seance = Seance.objects.get(id=session_id)
    except (Seance.DoesNotExist, ValueError, TypeError, ValidationError):
        return _json_err("Session not found", status=404)
    if seance.status != "planifiée":
        return _json_err("Only planned sessions can be cancelled")
    seance.status = "annulée"
    seance.save(update_fields=["status"])
    return _json_ok(message="Session cancelled")

@api_login_required
def api_alerts(request):
    if request.method != "GET":
        return _json_err("GET required", status=405)
    seance_alerts = SeanceAlert.objects.select_related("seance__patient", "seance__machine").order_by("-timestamp")
    level_filter = request.GET.get("level", "").strip().upper()
    status_filter = request.GET.get("status", "").strip().upper()
    session_id = request.GET.get("session_id", "").strip()
    date_filter = request.GET.get("date", "").strip()
    if level_filter in ("LOW", "MEDIUM", "HIGH"):
        seance_alerts = seance_alerts.filter(danger_level=level_filter)
    if status_filter in ("NEW", "ACK", "RESOLVED"):
        seance_alerts = seance_alerts.filter(status=status_filter)
    if session_id:
        try:
            seance_alerts = seance_alerts.filter(seance__id=session_id)
        except (ValueError, TypeError, ValidationError):
            return _json_err("Invalid session_id", status=404)
    if date_filter:
        try:
            seance_alerts = seance_alerts.filter(timestamp__date=date.fromisoformat(date_filter))
        except ValueError:
            return _json_err("Invalid date format — use YYYY-MM-DD")
    data = []
    for a in seance_alerts[:150]:
        data.append({
            "id": a.id,
            "source": "seances.Alert",
            "session_id": str(a.seance.id),
            "patient": str(a.seance.patient) if a.seance.patient else None,
            "machine": a.seance.machine.machine_id if a.seance.machine else None,
            "alert_type": a.alert_type,
            "message": a.message,
            "danger_level": a.danger_level,
            "severity": a.danger_level,
            "recommended_action": a.recommended_action,
            "status": a.status,
            "timestamp": a.timestamp.isoformat() if a.timestamp else None,
            "created_at": a.timestamp.isoformat() if a.timestamp else None,
        })
    m_alerts = Alerte.objects.select_related("reading__seance__patient", "reading__seance__machine").order_by("-timestamp")
    if status_filter in ("NEW", "ACK", "RESOLVED"):
        m_alerts = m_alerts.filter(status=status_filter)
    if level_filter in ("LOW", "MEDIUM", "HIGH"):
        # monitoring.Alerte stores RED/YELLOW (rendered as HIGH/MEDIUM/LOW
        # via _normalize_level) — apply the same level filter to this branch.
        wanted_levels = {
            "HIGH": ("RED", "HIGH"),
            "MEDIUM": ("YELLOW", "MEDIUM"),
            "LOW": ("LOW",),
        }[level_filter]
        m_alerts = m_alerts.filter(niveau__in=wanted_levels)
    if session_id:
        try:
            m_alerts = m_alerts.filter(reading__seance__id=session_id)
        except (ValueError, TypeError, ValidationError):
            return _json_err("Invalid session_id", status=404)
    if date_filter:
        try:
            m_alerts = m_alerts.filter(timestamp__date=date.fromisoformat(date_filter))
        except ValueError:
            pass
    for ma in m_alerts[:100]:
        seance = ma.reading.seance if ma.reading else None
        sev = ma.niveau.upper()
        if sev == "RED":
            sev = "HIGH"
        elif sev == "YELLOW":
            sev = "MEDIUM"
        data.append({
            "id": str(ma.id),
            "source": "monitoring.Alerte",
            "session_id": str(seance.id) if seance else None,
            "patient": str(seance.patient) if seance and seance.patient else None,
            "machine": seance.machine.machine_id if seance and seance.machine else None,
            "alert_type": "Measurement Threshold",
            "message": ma.message,
            "danger_level": sev,
            "severity": sev,
            "recommended_action": "Check patient status and machine parameters",
            "status": ma.status,
            "timestamp": ma.timestamp.isoformat() if ma.timestamp else None,
            "created_at": ma.timestamp.isoformat() if ma.timestamp else None,
        })
    return _json_ok(data, count=len(data))

@csrf_exempt
@api_login_required
def api_alert_ack(request, alert_id):
    if request.method != "POST":
        return _json_err("POST required", status=405)
    try:
        m_alert = Alerte.objects.get(id=alert_id)
        m_alert.status = "ACK"
        m_alert.save(update_fields=["status"])
        return _json_ok(message="Alert acknowledged")
    except (Alerte.DoesNotExist, ValueError, TypeError, ValidationError):
        try:
            alert_int_id = int(alert_id)
            s_alert = SeanceAlert.objects.get(id=alert_int_id)
            s_alert.status = "ACK"
            s_alert.save(update_fields=["status"])
            return _json_ok(message="Alert acknowledged")
        except (ValueError, SeanceAlert.DoesNotExist):
            return _json_err("Alert not found", status=404)

@csrf_exempt
@api_login_required
def api_alert_resolve(request, alert_id):
    if request.method != "POST":
        return _json_err("POST required", status=405)
    try:
        m_alert = Alerte.objects.get(id=alert_id)
        m_alert.status = "RESOLVED"
        m_alert.save(update_fields=["status"])
        return _json_ok(message="Alert resolved")
    except (Alerte.DoesNotExist, ValueError, TypeError, ValidationError):
        try:
            alert_int_id = int(alert_id)
            s_alert = SeanceAlert.objects.get(id=alert_int_id)
            s_alert.status = "RESOLVED"
            s_alert.save(update_fields=["status"])
            return _json_ok(message="Alert resolved")
        except (ValueError, SeanceAlert.DoesNotExist):
            return _json_err("Alert not found", status=404)

@api_login_required
def api_dashboard(request):
    if request.method != "GET":
        return _json_err("GET required", status=405)
    today = timezone.localdate()
    kpis = {
        "active_sessions": Seance.objects.filter(status="en cours").count(),
        "available_machines": Machine.objects.filter(status="Prete").count(),
        "total_machines": Machine.objects.count(),
        "active_alerts": Alerte.objects.filter(status="NEW").count()
        + SeanceAlert.objects.filter(status="NEW").count(),
        "patients_count": Patient.objects.count(),
        "today_sessions": Seance.objects.filter(session_date=today).count(),
    }
    return JsonResponse({"success": True, "kpis": kpis})

@device_token_required
def api_seance_debit(request):
    if request.method != "GET":
        return _json_err("GET required", status=405)
    machine_id = request.GET.get("machine_id", "").strip()
    if not machine_id:
        return _json_err("machine_id query parameter is required")
    try:
        machine = Machine.objects.get(machine_id=machine_id)
    except Machine.DoesNotExist:
        return _json_err("Machine not found", status=404)
    seance = Seance.objects.filter(machine=machine, status="en cours").first()
    if not seance:
        return JsonResponse({
            "debit": 60,
            "machine_id": machine_id,
            "seance_id": None,
            "note": "No active session — using default interval",
        })
    return JsonResponse({"debit": seance.debit, "machine_id": machine_id, "seance_id": str(seance.id)})

@csrf_exempt
@device_token_required
def push_measurement(request):
    if request.method != "POST":
        return _json_err("POST required", status=405)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return _json_err("Invalid JSON body", status=400)
    if not isinstance(data, dict):
        return _json_err("JSON object body required", status=400)
    try:
        machine_id = data.get("machine_id")
        machine = Machine.objects.get(machine_id=machine_id)
        seance = Seance.objects.filter(machine=machine, status="en cours").first()
        if not seance:
            return _json_err("No active seance for this machine", status=400)
        measurement = LiveMeasurement.objects.create(
            seance=seance,
            Debit_sang=data.get("Qb"),
            Taux_UF=data.get("UF_rate"),
            PA=data.get("PA"),
            PTM=data.get("PTM"),
            PV=data.get("PV"),
            Volume_UF=data.get("UF_volume"),
            Heparine=data.get("Heparin"),
        )
        monitoring_alerts_created, seance_alerts_created = persist_measurement_alerts(
            seance, measurement
        )
        return JsonResponse({
            "success": True,
            "id": str(measurement.id),
            "monitoring_alerts_created": monitoring_alerts_created,
            "seance_alerts_created": seance_alerts_created,
        })
    except Machine.DoesNotExist:
        return _json_err("Machine not found", status=404)
    except (TypeError, ValueError, ValidationError) as e:
        return _json_err(f"Invalid measurement value: {e}", status=400)
    except Exception as e:
        return _json_err(str(e), status=500)

def _normalize_level(level):
    """monitoring.Alerte stores RED/YELLOW; seances.Alert stores HIGH/MEDIUM.
    Normalize to the web/Flutter convention HIGH/MEDIUM/LOW."""
    lvl = (level or "").upper()
    if lvl == "RED":
        return "HIGH"
    if lvl == "YELLOW":
        return "MEDIUM"
    return lvl


def _live_alerts(limit=50):
    """Merged, normalized alert feed used by the web live pages
    (/api/real-monitoring/) and the mobile live page (/api/monitoring/live/).
    Combines monitoring.Alerte (threshold engine) and seances.Alert
    (analyser_mesure) so every surface shows the same alerts."""
    items = []

    for a in Alerte.objects.select_related(
        "reading__seance__machine", "reading__seance__patient"
    ).order_by("-timestamp")[:limit]:
        seance = a.reading.seance if a.reading else None
        items.append({
            "id": str(a.id),
            "source": "monitoring.Alerte",
            "niveau": _normalize_level(a.niveau),
            "message": a.message,
            "status": a.status,
            "machine": seance.machine.machine_id if seance and seance.machine else None,
            "time": a.timestamp.isoformat() if a.timestamp else None,
            "timestamp": a.timestamp.isoformat() if a.timestamp else None,
        })

    for a in SeanceAlert.objects.select_related(
        "seance__machine", "seance__patient"
    ).order_by("-timestamp")[:limit]:
        items.append({
            "id": str(a.id),
            "source": "seances.Alert",
            "niveau": _normalize_level(a.danger_level),
            "message": a.message,
            "status": a.status,
            "machine": a.seance.machine.machine_id if a.seance.machine else None,
            "time": a.timestamp.isoformat() if a.timestamp else None,
            "timestamp": a.timestamp.isoformat() if a.timestamp else None,
        })

    items.sort(key=lambda x: x["timestamp"] or "", reverse=True)
    return items[:limit]


@api_login_required
def real_monitoring(request):
    measurements = LiveMeasurement.objects.select_related("seance__machine", "seance__patient").order_by("-timestamp")[:20]
    data = []
    for m in measurements:
        alerts = list(m.alertes.values("id", "niveau", "message", "status", "timestamp"))
        for a in alerts:
            a["id"] = str(a["id"])
            a["niveau"] = _normalize_level(a["niveau"])
            if a.get("timestamp"):
                a["timestamp"] = a["timestamp"].isoformat()
        levels = {a["niveau"] for a in alerts if a.get("status") == "NEW"}
        if "HIGH" in levels:
            status_label = "CRITICAL"
        elif "MEDIUM" in levels:
            status_label = "WARNING"
        else:
            status_label = "NORMAL"
        data.append({
            "id": str(m.id),
            "machine": str(m.seance.machine) if m.seance.machine else None,
            "machine_id": m.seance.machine.machine_id if m.seance.machine else None,
            "patient": str(m.seance.patient) if m.seance.patient else None,
            "seance_id": str(m.seance.id),
            "timestamp": m.timestamp.isoformat() if m.timestamp else None,
            "time": m.timestamp.isoformat() if m.timestamp else None,
            "Debit_sang": m.Debit_sang,
            "Taux_UF": m.Taux_UF,
            "PA": m.PA,
            "PTM": m.PTM,
            "PV": m.PV,
            "Volume_UF": m.Volume_UF,
            "Heparine": m.Heparine,
            # Web JS aliases (dashboard.html / surveillance.html):
            "Qb": m.Debit_sang,
            "UF": m.Volume_UF,
            "status": status_label,
            "alerts": alerts,
        })
    return JsonResponse({
        "success": True,
        "measurements": data,
        "alerts": _live_alerts(),
        "last_update": timezone.now().isoformat(),
    })

@api_login_required
def api_monitoring_live(request):
    if request.method != "GET":
        return _json_err("GET required", status=405)
    active_seances = Seance.objects.filter(status="en cours").select_related("patient", "machine")
    sessions_data = []
    for seance in active_seances:
        last = LiveMeasurement.objects.filter(seance=seance).order_by("-timestamp").first()
        sessions_data.append({
            "seance_id": str(seance.id),
            "patient": str(seance.patient) if seance.patient else None,
            "machine": seance.machine.machine_id if seance.machine else None,
            "debit": seance.debit,
            "Debit_sang": last.Debit_sang if last else None,
            "Taux_UF": last.Taux_UF if last else None,
            "PA": last.PA if last else None,
            "PTM": last.PTM if last else None,
            "PV": last.PV if last else None,
            "Volume_UF": last.Volume_UF if last else None,
            "Heparine": last.Heparine if last else None,
            "timestamp": last.timestamp.isoformat() if last and last.timestamp else None,
        })
    recent_alerts = [a for a in _live_alerts(limit=20) if a["status"] == "NEW"]
    return JsonResponse({
        "success": True,
        "sessions": sessions_data,
        "alerts": recent_alerts,
        "last_update": timezone.now().isoformat(),
    })

def _measurement_payload(measurement):
    """Maps a LiveMeasurement to the Qb/PA/PTM/PV/UF boxes + machine/status
    block used by the monitoring dashboard (monitoring/templates/dashboard.html)."""
    alert_levels = {
        (a.niveau or "").upper()
        for a in measurement.alertes.filter(status="NEW")
    }
    if alert_levels & {"HIGH", "RED"}:
        status_label = "CRITICAL"
    elif alert_levels & {"MEDIUM", "YELLOW"}:
        status_label = "WARNING"
    else:
        status_label = "NORMAL"
    return {
        "machine": str(measurement.seance.machine) if measurement.seance and measurement.seance.machine else None,
        "machine_id": measurement.seance.machine.machine_id if measurement.seance and measurement.seance.machine else None,
        "patient": str(measurement.seance.patient) if measurement.seance and measurement.seance.patient else None,
        "Qb": measurement.Debit_sang,
        "PA": measurement.PA,
        "PTM": measurement.PTM,
        "PV": measurement.PV,
        "UF": measurement.Volume_UF,
        "status": status_label,
        "time": measurement.timestamp.isoformat() if measurement.timestamp else None,
    }


@api_login_required
def api_monitoring(request):
    """Mobile counterpart of the /monitoring/ dashboard page.

    Reproduces `monitoring/views.py dashboard()`: the KPI cards, the live
    dialysis measurement block, the real-time alerts and the login/logout
    history table (with the same day/q/role/sort/status filters).
    """
    if request.method != "GET":
        return _json_err("GET required", status=405)

    # KPIs — identical querysets to the web dashboard.
    kpis = {
        "doctors": User.objects.filter(role__name__in=["Docteur", "Admin"]).count(),
        "nurses": User.objects.filter(role__name__iexact="Infirmier").count(),
        "active_users": User.objects.filter(etat=True).count(),
        "machines_available": Machine.objects.filter(status="Prete").count(),
        "machines_total": Machine.objects.count(),
    }

    # Login/logout history — identical filters/sorting to the web dashboard.
    selected_day = (request.GET.get("day") or "").strip()
    q = (request.GET.get("q") or "").strip()
    role_filter = (request.GET.get("role") or "").strip()
    sort = (request.GET.get("sort") or "-login_at").strip()
    status = (request.GET.get("status") or "").strip()

    allowed_sorts = {"login_at", "-login_at", "username", "-username"}
    if sort not in allowed_sorts:
        sort = "-login_at"

    qs = UserActivity.objects.select_related("user", "user__role")
    if selected_day:
        qs = qs.filter(login_at__date=selected_day)
    if q:
        qs = qs.filter(
            Q(user__username__icontains=q) |
            Q(user__email__icontains=q)
        )
    if role_filter in ("Docteur", "Infirmier", "Admin"):
        qs = qs.filter(user__role__name__iexact=role_filter)
    if sort in ("username", "-username"):
        prefix = "-" if sort.startswith("-") else ""
        qs = qs.order_by(f"{prefix}user__username", "-login_at")
    else:
        qs = qs.order_by(sort)
    if status == "ongoing":
        qs = qs.filter(logout_at__isnull=True)

    activity = [
        {
            "username": a.user.username,
            "email": a.user.email or "",
            "role": a.user.role.name if a.user.role else "",
            "login_at": a.login_at.isoformat() if a.login_at else None,
            "logout_at": a.logout_at.isoformat() if a.logout_at else None,
        }
        for a in qs[:50]
    ]

    # Live measurement — latest reading drives the Qb/PA/PTM/PV/UF boxes.
    last = (
        LiveMeasurement.objects
        .select_related("seance__machine", "seance__patient")
        .order_by("-timestamp")
        .first()
    )
    measurement = _measurement_payload(last) if last else None

    recent_alerts = (
        Alerte.objects
        .select_related("reading__seance__machine")
        .order_by("-timestamp")[:20]
    )
    alerts = [
        {
            "id": str(a.id),
            "niveau": a.niveau,
            "message": a.message,
            "status": a.status,
            "machine": a.reading.seance.machine.machine_id
            if a.reading and a.reading.seance and a.reading.seance.machine
            else None,
            "time": a.timestamp.isoformat() if a.timestamp else None,
        }
        for a in recent_alerts
    ]

    return JsonResponse({
        "success": True,
        "kpis": kpis,
        "measurement": measurement,
        "alerts": alerts,
        "activity": activity,
        "last_update": timezone.now().isoformat(),
    })

def _staff_profile_payload(user, profile=None):
    if profile is None:
        profile = Profile.objects.filter(user=user).first()
    join_date = user.date_inscription
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email or "",
        "phone": user.phone_number or "",
        "address": user.adress or "",
        "role": user.role.name if user.role else "",
        "specialite": user.specialite or "",
        "etat": bool(user.etat),
        "status_label": "Actif" if user.etat else "Inactif",
        "date_inscription": str(join_date) if join_date else None,
        "member_since": join_date.strftime("%B %Y") if join_date else "",
        "seniority_label": _seniority_label(join_date),
        "bio": profile.bio if profile else "",
        "formation": profile.formation if profile else "",
        "experience": profile.experience if profile else "",
        "first_login": user.first_login,
        "photo_url": (
            profile.image.url if profile and profile.image else ""
        ),
    }


@csrf_exempt
@api_login_required
def api_doctors(request):
    """Mobile counterpart of accounts.views.docteurs_list + add_doctor."""
    current_user = request.current_user
    if request.method == "POST":
        if not _has_role(current_user, "Admin"):
            return _json_err("Insufficient permissions — Admin only", status=403)
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return _json_err("Invalid JSON")
        full_name = str(body.get("fullName") or body.get("username") or "").strip()
        email = str(body.get("email") or "").strip().lower()
        speciality = body.get("speciality") or body.get("specialite") or ""
        phone = body.get("phone") or body.get("phone_number") or ""
        if not full_name or not email:
            return _json_err("Nom et email sont obligatoires.")
        if User.objects.filter(email=email).exists():
            return _json_err("Cet email existe déjà.")
        if User.objects.filter(username=full_name).exists():
            return _json_err("Ce nom d'utilisateur existe déjà.")
        password = secrets.token_urlsafe(8)[:10]
        role_doctor, _ = Role.objects.get_or_create(name="Docteur")
        user = User.objects.create(
            username=full_name,
            email=email,
            specialite=speciality,
            phone_number=phone,
            role=role_doctor,
            etat=False,
            password=make_password(password),
        )
        return _json_ok(
            _staff_profile_payload(user),
            generated_password=password,
            message=f"Docteur ajouté avec succès ! Mot de passe : {password}",
        )

    if request.method != "GET":
        return _json_err("GET or POST required", status=405)
    if not _has_role(current_user, "Admin"):
        return _json_err("Insufficient permissions — Admin only", status=403)

    search = request.GET.get("search", "").strip()
    role = request.GET.get("role", "").strip()
    status = request.GET.get("status", "").strip()

    doctors_qs = User.objects.select_related("role").filter(
        role__name__in=["Docteur", "Admin"]
    )
    total_doctors = doctors_qs.count()
    isadmin_count = doctors_qs.filter(role__name="Admin").count()
    is_actif_count = doctors_qs.filter(etat=True).count()

    if search:
        doctors_qs = doctors_qs.filter(username__icontains=search)
    if role == "admin":
        doctors_qs = doctors_qs.filter(role__name="Admin")
    elif role == "doctor":
        doctors_qs = doctors_qs.filter(role__name="Docteur")
    if status == "active":
        doctors_qs = doctors_qs.filter(etat=True)
    elif status == "inactive":
        doctors_qs = doctors_qs.filter(etat=False)

    doctors_qs = doctors_qs.order_by("username")
    profile_qs = Profile.objects.all()
    doctors = []
    for d in doctors_qs:
        profile = profile_qs.filter(user=d).first()
        experience_years = profile.experience if profile and profile.experience else 0
        payload = _staff_profile_payload(d, profile)
        payload.update({
            "fullName": f"Dr.{d.username}",
            "speciality": d.specialite or "Généraliste",
            "roleLabel": d.role.name if d.role else "",
            "rating": 0,  # aucune donnée de notation en base
            "patientsCount": getattr(d, "patients_count", 0) or 0,
            "sessionsCount": 0,  # aucune relation médecin->séance dans le schéma
            "experienceYears": experience_years,
        })
        doctors.append(payload)
    return _json_ok(
        doctors,
        count=len(doctors),
        kpis={
            "total_doctors": total_doctors,
            "isadmin_count": isadmin_count,
            "isActif_count": is_actif_count,
        },
    )


@api_login_required
def api_doctor_detail(request, doctor_id):
    current_user = request.current_user
    if not _has_role(current_user, "Admin"):
        return _json_err("Insufficient permissions — Admin only", status=403)
    if request.method != "GET":
        return _json_err("GET required", status=405)
    doctor = (
        User.objects.filter(Q(role__name="Docteur") | Q(role__name="Admin"), id=doctor_id)
        .select_related("role")
        .first()
    )
    if not doctor:
        return _json_err("Médecin introuvable.", status=404)
    profile = Profile.objects.filter(user=doctor).first()
    return _json_ok(_staff_profile_payload(doctor, profile))


@csrf_exempt
@api_login_required
def api_nurses(request):
    """Mobile counterpart of accounts.views.nurses_list + ajout_infirmier."""
    current_user = request.current_user
    if request.method == "POST":
        if not _has_role(current_user, "Admin", "Docteur"):
            return _json_err("Insufficient permissions", status=403)
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return _json_err("Invalid JSON")
        nom = str(body.get("nom") or body.get("username") or "").strip()
        email = str(body.get("email") or "").strip().lower()
        telephone = str(body.get("telephone") or body.get("phone") or "").strip()
        if not nom or not email:
            return _json_err("Nom et email sont obligatoires.")
        if User.objects.filter(email=email).exists():
            return _json_err("Cet email existe déjà.")
        if User.objects.filter(username=nom).exists():
            return _json_err("Ce nom d'utilisateur existe déjà.")
        password = secrets.token_urlsafe(8)[:10]
        role_infirmier, _ = Role.objects.get_or_create(name="Infirmier")
        user = User(
            username=nom,
            email=email,
            phone_number=telephone,
            etat=False,
            password=make_password(password),
            role=role_infirmier,
        )
        user.save()
        return _json_ok(
            _staff_profile_payload(user),
            generated_password=password,
            message=f"Infirmier ajouté. Mot de passe : {password}",
        )

    if request.method != "GET":
        return _json_err("GET or POST required", status=405)
    if not _has_role(current_user, "Admin", "Docteur"):
        return _json_err("Insufficient permissions", status=403)

    search_query = request.GET.get("search", "").strip()
    status_filter = request.GET.get("status", "").strip().lower()

    nurses_qs = (
        User.objects.select_related("role")
        .filter(role__name__iexact="Infirmier")
        .order_by("username")
    )
    total_nurses = nurses_qs.count()
    kpi_active_nurses = nurses_qs.filter(etat=True).count()
    if search_query:
        nurses_qs = nurses_qs.filter(
            Q(username__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(phone_number__icontains=search_query)
        )
    if status_filter == "active":
        nurses_qs = nurses_qs.filter(etat=True)
    elif status_filter == "inactive":
        nurses_qs = nurses_qs.filter(etat=False)

    nurses = []
    for n in nurses_qs:
        assigned_mgr = getattr(n, "assigned_doctors", None)
        doctor_names = [d.username for d in assigned_mgr.all()] if assigned_mgr else []
        payload = _staff_profile_payload(n)
        payload.update({
            "firstName": n.username,
            "lastName": "",
            "assignedDoctorsText": ", ".join(doctor_names) if doctor_names else "Aucun",
            "patientsCount": 0,
            "activeSessions": 0,
            "scheduledSessions": 0,
        })
        nurses.append(payload)
    return _json_ok(
        nurses,
        count=len(nurses),
        kpis={
            "total_nurses": total_nurses,
            "kpi_active_nurses": kpi_active_nurses,
            "kpi_total_patients": Patient.objects.count(),
            "kpi_active_sessions": Seance.objects.filter(status="en cours").count(),
            "kpi_scheduled_sessions": Seance.objects.filter(status="planifiée").count(),
            "kpi_avg_load": round(Patient.objects.count() / total_nurses) if total_nurses else 0,
        },
    )


@api_login_required
def api_nurse_detail(request, nurse_id):
    current_user = request.current_user
    if not _has_role(current_user, "Admin", "Docteur"):
        return _json_err("Insufficient permissions", status=403)
    if request.method != "GET":
        return _json_err("GET required", status=405)
    nurse = (
        User.objects.filter(role__name="Infirmier", id=nurse_id)
        .select_related("role")
        .first()
    )
    if not nurse:
        return _json_err("Infirmier introuvable.", status=404)
    profile = Profile.objects.filter(user=nurse).first()
    return _json_ok(_staff_profile_payload(nurse, profile))


@csrf_exempt
@api_login_required
def api_profile(request):
    """Mobile counterpart of accounts.views.profile."""
    current_user = request.current_user
    profile, _ = Profile.objects.get_or_create(user=current_user)

    if request.method == "GET":
        return _json_ok(_staff_profile_payload(current_user, profile))

    if request.method not in ("PUT", "POST"):
        return _json_err("GET, PUT or POST required", status=405)

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _json_err("Invalid JSON")

    old_password = body.get("old_password") or ""
    new_password = body.get("password") or ""

    if current_user.first_login:
        if not old_password or not new_password:
            return _json_err(
                "Vous devez changer votre mot de passe pour pouvoir enregistrer."
            )
        if not check_password(old_password, current_user.password):
            return _json_err("Ancien mot de passe incorrect.")
        current_user.password = make_password(new_password)
        current_user.first_login = False
        current_user.save()
        return _json_ok(
            _staff_profile_payload(current_user, profile),
            message="Mot de passe mis à jour avec succès !",
        )

    profile.bio = body.get("bio", profile.bio) or ""
    profile.formation = body.get("formation", profile.formation) or ""
    profile.experience = body.get("experience", profile.experience) or ""
    profile.save()

    # Photo de profil — same contract as accounts.views.profile:
    # a base64 data-URL "cropped_image" (web CropperJS) is stored directly;
    # a raw file upload is not supported through this JSON endpoint.
    cropped_data = body.get("cropped_image")
    if cropped_data:
        try:
            format, imgstr = cropped_data.split(";base64,")
            ext = format.split("/")[-1]
            file_name = f"profile_{uuid.uuid4()}.{ext}"
            profile.image = ContentFile(base64.b64decode(imgstr), name=file_name)
            profile.save()
        except (ValueError, TypeError, binascii.Error):
            return _json_err("Image invalide.")

    new_phone = body.get("phone_number")
    if new_phone is not None and new_phone != current_user.phone_number:
        current_user.phone_number = new_phone
        current_user.save(update_fields=["phone_number"])

    new_adresse = body.get("adress") or body.get("address")
    if new_adresse is not None and new_adresse != current_user.adress:
        current_user.adress = new_adresse
        current_user.save(update_fields=["adress"])

    new_email = body.get("email")
    if new_email is not None and new_email != current_user.email:
        current_user.email = new_email
        current_user.save(update_fields=["email"])

    if new_password:
        if not check_password(old_password, current_user.password):
            return _json_err("Ancien mot de passe incorrect.")
        current_user.password = make_password(new_password)
        current_user.save(update_fields=["password"])

    return _json_ok(
        _staff_profile_payload(current_user, profile),
        message="Profil mis à jour avec succès !",
    )


def _device_dict(d):
    return {
        "id": str(d.id),
        "raspi_id": d.raspi_id,
        "description": d.description,
        "is_active": d.is_active,
        "last_seen": d.last_seen.isoformat() if d.last_seen else None,
        "machine": {
            "id": d.machine.id,
            "machine_id": d.machine.machine_id,
        } if d.machine_id else None,
    }


@csrf_exempt
@api_login_required
def api_devices(request):
    """Mobile counterpart of machines.views.raspi_management + add_raspi."""
    current_user = request.current_user
    if request.method == "POST":
        if not _has_role(current_user, "Admin"):
            return _json_err("Insufficient permissions — Admin only", status=403)
        try:
            body = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return _json_err("Invalid JSON")
        raspi_id = str(body.get("raspi_id", "")).strip()
        description = str(body.get("description", "")).strip()
        if not raspi_id:
            return _json_err("raspi_id requis")
        if RaspiDevice.objects.filter(raspi_id=raspi_id).exists():
            return _json_err("Ce Raspi existe déjà", status=409)
        device = RaspiDevice.objects.create(raspi_id=raspi_id, description=description)
        return _json_ok(_device_dict(device), message="Appareil ajouté avec succès !")

    if request.method != "GET":
        return _json_err("GET or POST required", status=405)
    if not _has_role(current_user, "Admin"):
        return _json_err("Insufficient permissions — Admin only", status=403)

    devices = list(RaspiDevice.objects.select_related("machine").order_by("raspi_id"))
    machines = Machine.objects.all().order_by("machine_id")
    assigned_machine_ids = [d.machine_id for d in devices if d.machine_id]
    total = len(devices)
    assigned = sum(1 for d in devices if d.machine_id)
    free = sum(1 for d in devices if not d.machine_id)
    inactive = sum(
        1
        for d in devices
        if d.last_seen is None
        or (timezone.now() - d.last_seen).total_seconds() > 14400
    )
    return _json_ok(
        [_device_dict(d) for d in devices],
        count=total,
        stats={
            "total": total,
            "assigned": assigned,
            "free": free,
            "inactive": inactive,
        },
        machines=[
            {"id": m.id, "machine_id": m.machine_id, "location": m.location}
            for m in machines
        ],
        assigned_machine_ids=assigned_machine_ids,
    )


@csrf_exempt
@api_login_required
def api_device_assign(request, raspi_id):
    """Same contract as machines.views.assign_machine."""
    if request.method != "POST":
        return _json_err("POST required", status=405)
    current_user = request.current_user
    if not _has_role(current_user, "Admin", "Infirmier", "Docteur"):
        return _json_err("Insufficient permissions", status=403)
    try:
        body = json.loads(request.body or "{}")
        machine_id = body.get("machine_id")
    except json.JSONDecodeError:
        return _json_err("JSON invalide")
    try:
        device = RaspiDevice.objects.get(id=raspi_id)
    except (RaspiDevice.DoesNotExist, ValueError, TypeError, ValidationError):
        return _json_err("Raspi introuvable", status=404)
    if machine_id:
        try:
            machine = Machine.objects.get(id=machine_id)
        except (Machine.DoesNotExist, ValueError, TypeError, ValidationError):
            return _json_err("Machine introuvable", status=404)
        RaspiDevice.objects.filter(machine=machine).exclude(id=raspi_id).update(machine=None)
        device.machine = machine
    else:
        device.machine = None
    device.save(update_fields=["machine"])
    return _json_ok({
        "raspi_id": device.raspi_id,
        "machine": device.machine.machine_id if device.machine else None,
    })
