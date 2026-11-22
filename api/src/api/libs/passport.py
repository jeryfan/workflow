from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from api.configs import app_config
from api.exceptions import UnauthorizedError

JWT_ISSUER = "workflow"


class PassportService:
    def __init__(self) -> None:
        self.sk = app_config.SECRET_KEY

    def issue(self, payload: dict[str, Any], expires_in: timedelta) -> str:
        now = datetime.now(UTC)
        claims = {"iat": now, "iss": JWT_ISSUER, **payload, "exp": now + expires_in}
        return jwt.encode(claims, self.sk, algorithm="HS256")

    def verify(self, token: str | bytes) -> dict[str, Any]:
        try:
            return jwt.decode(token, self.sk, algorithms=["HS256"], issuer=JWT_ISSUER)
        except jwt.ExpiredSignatureError:
            raise UnauthorizedError("Token has expired") from None
        except jwt.PyJWTError:
            raise UnauthorizedError("Invalid token") from None
