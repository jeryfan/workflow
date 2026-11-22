from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from email_validator import EmailNotValidError, validate_email

from api.db.store import (
    AlreadySetupError,
    NewSession,
    SessionRotation,
    SetupAccount,
    Store,
)
from api.models.entities import Actor

ACCESS_TOKEN_TTL = 60 * 60
REFRESH_TOKEN_TTL = 30 * 24 * 60 * 60
LOGIN_FAILURE_LIMIT = 5
LOGIN_THROTTLE_WINDOW = 15 * 60


class InvalidSetupInput(ValueError):
    pass


class AuthError(RuntimeError):
    pass


class AuthenticationFailed(AuthError):
    pass


class InvalidCsrf(AuthError):
    pass


class LoginThrottled(AuthError):
    pass


class AccountRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    NORMAL = "normal"


@dataclass(frozen=True, slots=True)
class SetupCommand:
    email: str
    name: str
    password: str
    language: str = "en-US"


@dataclass(frozen=True, slots=True)
class LoginCommand:
    email: str
    password: str
    client_ip: str


@dataclass(frozen=True, slots=True)
class ProvisionMemberCommand:
    email: str
    name: str
    password: str
    role: AccountRole


@dataclass(frozen=True, slots=True)
class Principal:
    account_id: str
    email: str
    name: str
    role: AccountRole
    session_id: str

    @property
    def actor(self) -> Actor:
        return Actor(id=self.account_id, name=self.name, email=self.email)


@dataclass(frozen=True, slots=True)
class IssuedSession:
    access_token: str
    refresh_token: str
    csrf_token: str
    access_expires_at: int
    refresh_expires_at: int


@dataclass(frozen=True, slots=True)
class _AuthenticatedSession:
    principal: Principal
    csrf_token_hash: str


class AuthService:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.password_hasher = PasswordHasher()
        self._dummy_password_hash = self.password_hasher.hash("DummyPassword123")

    def setup_at(self) -> int | None:
        return self.store.setup_at()

    def setup(self, command: SetupCommand) -> None:
        if self.store.setup_at() is not None:
            raise AlreadySetupError
        email, normalized_email, name = self._validate_identity(command.email, command.name)
        self._validate_password(command.password)
        password_hash = self.password_hasher.hash(command.password)
        self.store.setup_owner(
            SetupAccount(
                id=str(uuid4()),
                email=email,
                normalized_email=normalized_email,
                name=name,
                password_hash=password_hash,
                language=command.language,
                created_at=int(time.time()),
            )
        )

    def login(self, command: LoginCommand) -> IssuedSession:
        normalized_email = self._normalize_login_email(command.email)
        subjects = self._login_subjects(normalized_email, command.client_ip)
        now = int(time.time())
        self._check_login_throttle(subjects, now)
        account = self.store.account_for_login(normalized_email)
        if account is None:
            self._fail_login(subjects, verify_dummy=True)

        try:
            self.password_hasher.verify(account.password_hash, command.password)
        except VerificationError:
            self._fail_login(subjects, verify_dummy=False)
        if account.status != "active" or account.role is None:
            self._fail_login(subjects, verify_dummy=False)

        issued = self._issue_tokens(now)
        session = NewSession(
            id=str(uuid4()),
            account_id=account.id,
            access_token_hash=self._token_digest(issued.access_token),
            refresh_token_hash=self._token_digest(issued.refresh_token),
            csrf_token_hash=self._token_digest(issued.csrf_token),
            access_expires_at=issued.access_expires_at,
            refresh_expires_at=issued.refresh_expires_at,
            created_at=now,
        )
        if not self.store.create_session(session, command.client_ip, subjects):
            self._fail_login(subjects, verify_dummy=False)
        return issued

    def reject_login(self, email: str, client_ip: str) -> NoReturn:
        normalized_email = self._normalize_login_email(email)
        subjects = self._login_subjects(normalized_email, client_ip)
        self._check_login_throttle(subjects, int(time.time()))
        self._fail_login(subjects, verify_dummy=True)

    def authenticate_access(self, token: str) -> Principal:
        return self._authenticate(token).principal

    def authenticate_request(
        self,
        access_token: str | None,
        csrf_cookie: str | None,
        csrf_header: str | None,
    ) -> Principal:
        if not access_token:
            raise AuthenticationFailed
        authenticated = self._authenticate(access_token)
        if (
            not csrf_cookie
            or not csrf_header
            or not hmac.compare_digest(csrf_cookie.encode("utf-8"), csrf_header.encode("utf-8"))
            or not hmac.compare_digest(self._token_digest(csrf_cookie), authenticated.csrf_token_hash)
        ):
            raise InvalidCsrf
        return authenticated.principal

    def refresh(self, refresh_token: str | None) -> IssuedSession:
        if not refresh_token:
            raise AuthenticationFailed
        now = int(time.time())
        issued = self._issue_tokens(now)
        rotated = self.store.rotate_session(
            SessionRotation(
                old_refresh_token_hash=self._token_digest(refresh_token),
                access_token_hash=self._token_digest(issued.access_token),
                refresh_token_hash=self._token_digest(issued.refresh_token),
                csrf_token_hash=self._token_digest(issued.csrf_token),
                access_expires_at=issued.access_expires_at,
                refresh_expires_at=issued.refresh_expires_at,
                refreshed_at=now,
            )
        )
        if not rotated:
            raise AuthenticationFailed
        return issued

    def logout(self, principal: Principal) -> None:
        self.store.revoke_session(principal.session_id, int(time.time()))

    def provision_member(self, command: ProvisionMemberCommand) -> str:
        if command.role is AccountRole.OWNER:
            raise InvalidSetupInput("The owner account can only be created during setup")
        email, normalized_email, name = self._validate_identity(command.email, command.name)
        self._validate_password(command.password)
        account_id = str(uuid4())
        self.store.create_member(
            SetupAccount(
                id=account_id,
                email=email,
                normalized_email=normalized_email,
                name=name,
                password_hash=self.password_hasher.hash(command.password),
                language="en-US",
                created_at=int(time.time()),
            ),
            command.role.value,
        )
        return account_id

    def _authenticate(self, token: str) -> _AuthenticatedSession:
        stored = self.store.authenticate_access(self._token_digest(token), int(time.time()))
        if stored is None:
            raise AuthenticationFailed
        principal = Principal(
            account_id=stored.account_id,
            email=stored.email,
            name=stored.name,
            role=AccountRole(stored.role),
            session_id=stored.session_id,
        )
        return _AuthenticatedSession(principal=principal, csrf_token_hash=stored.csrf_token_hash)

    def _check_login_throttle(self, subjects: tuple[str, str], now: int) -> None:
        if self.store.login_is_throttled(subjects, now):
            raise LoginThrottled

    def _fail_login(self, subjects: tuple[str, str], verify_dummy: bool) -> NoReturn:
        if verify_dummy:
            with suppress(VerificationError):
                self.password_hasher.verify(self._dummy_password_hash, "NotThePassword123")
        self.store.record_login_failures(
            subjects,
            int(time.time()),
            LOGIN_FAILURE_LIMIT,
            LOGIN_THROTTLE_WINDOW,
        )
        raise AuthenticationFailed

    @staticmethod
    def _login_subjects(normalized_email: str, client_ip: str) -> tuple[str, str]:
        return f"email:{normalized_email}", f"ip:{client_ip}"

    @staticmethod
    def _normalize_login_email(email: str) -> str:
        try:
            return validate_email(email.strip(), check_deliverability=False).normalized.casefold()
        except EmailNotValidError:
            return email.strip().casefold()

    @staticmethod
    def _validate_identity(email: str, name: str) -> tuple[str, str, str]:
        normalized_name = name.strip()
        if not normalized_name or len(normalized_name) > 30:
            raise InvalidSetupInput("Name must contain between 1 and 30 characters")
        try:
            normalized_email = validate_email(email.strip(), check_deliverability=False).normalized
        except EmailNotValidError as error:
            raise InvalidSetupInput(str(error)) from error
        return normalized_email, normalized_email.casefold(), normalized_name

    @staticmethod
    def _validate_password(password: str) -> None:
        if (
            len(password) < 8
            or not any(character.isalpha() for character in password)
            or not any(character.isdigit() for character in password)
            or any(character.isspace() for character in password)
        ):
            raise InvalidSetupInput("Password must have letters, numbers, at least eight characters, and no whitespace")

    @staticmethod
    def _issue_tokens(now: int) -> IssuedSession:
        return IssuedSession(
            access_token=secrets.token_urlsafe(32),
            refresh_token=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            access_expires_at=now + ACCESS_TOKEN_TTL,
            refresh_expires_at=now + REFRESH_TOKEN_TTL,
        )

    @staticmethod
    def _token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
