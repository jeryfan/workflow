from starlette.exceptions import HTTPException as StarletteHTTPException


class BaseHTTPException(StarletteHTTPException):
    status_code: int = 400
    code: str = "bad_request"
    detail: str = ""

    def __init__(self, detail: str | None = None, headers: dict[str, str] | None = None):
        super().__init__(
            status_code=self.status_code,
            detail=detail or self.detail,
            headers=headers,
        )


class UnauthorizedError(BaseHTTPException):
    status_code = 401
    code = "unauthorized"
    detail = "Unauthorized"


class CsrfValidationError(BaseHTTPException):
    status_code = 403
    code = "invalid_csrf"
    detail = "A valid CSRF token is required"


class AuthenticationFailedError(BaseHTTPException):
    status_code = 401
    code = "authentication_failed"
    detail = "Authentication failed"


class InvalidRefreshTokenError(BaseHTTPException):
    status_code = 401
    code = "authentication_failed"
    detail = "Invalid refresh token"


class AccountUnavailableError(BaseHTTPException):
    status_code = 401
    code = "account_unavailable"
    detail = "Account is unavailable"


class TooManyRequestsError(BaseHTTPException):
    status_code = 429
    code = "too_many_requests"
    detail = "Too many requests"


class InvalidFileTypeError(BaseHTTPException):
    status_code = 400
    code = "invalid_file_type"
    detail = "Invalid file type"


class InvalidFileNameError(BaseHTTPException):
    status_code = 400
    code = "invalid_file_name"
    detail = "Invalid file name"


class FileTooLargeError(BaseHTTPException):
    status_code = 413
    code = "file_too_large"
    detail = "File too large"


class NotFoundError(BaseHTTPException):
    status_code = 404
    code = "not_found"
    detail = "Resource not found"


class AlreadySetupError(BaseHTTPException):
    status_code = 403
    code = "already_setup"
    detail = "Setup has already been completed"


class SetupRequiredError(BaseHTTPException):
    status_code = 400
    code = "setup_required"
    detail = "Setup must be completed first"


class DuplicateAccountError(BaseHTTPException):
    status_code = 400
    code = "duplicate_account"
    detail = "An account with this email already exists"


class InvalidSetupInput(BaseHTTPException):
    status_code = 400
    code = "invalid_argument"
    detail = "Invalid setup input"


class LoginThrottled(BaseHTTPException):
    status_code = 429
    code = "email_password_login_limit"
    detail = "Too many login failures"
