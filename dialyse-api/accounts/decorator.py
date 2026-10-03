import secrets

from functools import wraps
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect
from django.contrib import messages
from .models import User

def app_login_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        user_id = request.session.get("app_user_id")
        if not user_id:
            return redirect("accounts:login_view")

        try:
            request.current_user = User.objects.select_related("role").get(id=user_id)
        except User.DoesNotExist:
            request.session.flush()
            return redirect("accounts:login_view")
        if request.current_user.first_login:
            # Le nom de l'URL doit être EXACTEMENT celui de urls.py
            if request.resolver_match.url_name not in ["profile", "logout"]:
                messages.error(request, "Vous devez changer votre mot de passe avant d’accéder au site.")
                return redirect("accounts:profile")

        return view_func(request, *args, **kwargs)
    return _wrapped

def role_required(*allowed_roles, redirect_to="accounts:home"):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            user = getattr(request, "current_user", None)
            if not user:
                return redirect("accounts:login_view")

            role_name = (user.role.name or "").lower()
            allowed = {r.lower() for r in allowed_roles}

            if role_name not in allowed:
                return redirect(redirect_to)

            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator

def device_token_required(view_func):
    """Machine-to-machine authentication for device endpoints
    (/api/push/, /api/seance/debit/, /machines/raspi/heartbeat/).

    The client must send the shared secret in the X-Device-Token header.
    The secret comes from the DEVICE_TOKEN environment variable (same
    .env pattern as the rest of the project). Fail-closed: if the server
    has no token configured, every request is rejected."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        expected = (getattr(settings, "DEVICE_TOKEN", "") or "").strip()
        provided = (
            request.headers.get("X-Device-Token")
            or request.META.get("HTTP_X_DEVICE_TOKEN")
            or ""
        ).strip()
        if not expected:
            return JsonResponse(
                {"success": False, "error": "DEVICE_TOKEN not configured on server"},
                status=401,
            )
        if not provided or not secrets.compare_digest(
            provided.encode("utf-8"), expected.encode("utf-8")
        ):
            return JsonResponse(
                {"success": False, "error": "Authentication required"},
                status=401,
            )
        return view_func(request, *args, **kwargs)
    return _wrapped
