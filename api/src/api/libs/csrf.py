import hashlib
import hmac

from api.configs import app_config

CSRF_TOKEN_HEADER = "X-CSRF-Token"


def generate_csrf_token(refresh_token: str) -> str:
    return hmac.new(
        app_config.SECRET_KEY.encode("utf-8"),
        refresh_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
