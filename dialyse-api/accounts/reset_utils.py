
from django.conf import settings
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired

signer = TimestampSigner(salt=getattr(settings, "RESET_PASSWORD_SALT", "reset-password"))

def make_reset_token(user_id: int) -> str:
    return signer.sign(str(user_id))  # ex: "123:signature" [web:70]

def read_reset_token(token: str, max_age_seconds: int) -> int | None:
    try:
        user_id = signer.unsign(token, max_age=max_age_seconds)
        return int(user_id)
    except (BadSignature, SignatureExpired, ValueError):
        return None  # token invalide ou expiré [web:70]
