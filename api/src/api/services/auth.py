from __future__ import annotations

import asyncio
from functools import cache
from typing import NoReturn
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from api.configs import app_config
from api.exceptions import (
    AlreadySetupError,
    AuthenticationFailedError,
    DuplicateAccountError,
    InvalidSetupInput,
    LoginThrottled,
    SetupRequiredError,
)
from api.libs.datetime_utils import utc_now
from api.libs.passport import PassportService
from api.libs.rate_limiter import ErrorRateLimiter
from api.libs.token import (
    AuthTokens,
    consume_refresh_token,
    is_access_token_revoked,
    issue_tokens,
    revoke_access_token_id,
)
from api.models.account import Account
from api.models.app import App
from api.models.installation import InstallationState
from api.schemas.auth import (
    AccountRole,
    LoginCommand,
    Principal,
    ProvisionMemberCommand,
    SetupCommand,
)

from .base_service import BaseService


@cache
def _dummy_password_hash() -> str:
    return PasswordHasher().hash("DummyPassword123")


class AuthService(BaseService):
    def __init__(self, db) -> None:
        super().__init__(db)
        self.password_hasher = PasswordHasher()
        self.login_limiters = (
            ErrorRateLimiter("login_email_errors", 5, app_config.LOGIN_LOCKOUT_DURATION),
            ErrorRateLimiter("login_ip_errors", 5, app_config.LOGIN_LOCKOUT_DURATION),
        )

    async def setup_at(self) -> int | None:
        return await self.db.scalar(select(InstallationState.setup_at).limit(1))

    async def setup(self, command: SetupCommand) -> None:
        if await self.setup_at() is not None:
            raise AlreadySetupError
        if await self.db.scalar(select(Account.id).limit(1)) is not None:
            raise AlreadySetupError
        email, normalized_email, name = self._validate_identity(command.email, command.name)
        self._validate_password(command.password)
        now = int(utc_now().timestamp())
        account = self._new_account(
            email=email,
            normalized_email=normalized_email,
            name=name,
            password=command.password,
            language=command.language,
            role=AccountRole.OWNER,
            now=now,
        )
        self.db.add_all([account, InstallationState(id=1, setup_at=now)])
        try:
            await self.db.flush()
            await self.db.execute(
                update(App).values(
                    maintainer_id=account.id,
                    created_by_id=account.id,
                    updated_by_id=account.id,
                )
            )
            await self.db.commit()
        except IntegrityError as error:
            await self.db.rollback()
            if await self.setup_at() is not None or await self.db.scalar(select(Account.id).limit(1)) is not None:
                raise AlreadySetupError from error
            raise

    async def login(self, command: LoginCommand) -> AuthTokens:
        normalized_email = self._normalize_login_email(command.email)
        limiter_keys = (f"email:{normalized_email}", f"ip:{command.client_ip}")
        for limiter, key in zip(self.login_limiters, limiter_keys, strict=True):
            if await limiter.is_limited(key):
                raise LoginThrottled
        account = await self.db.scalar(select(Account).where(Account.normalized_email == normalized_email))
        password_hash = account.password_hash if account is not None else _dummy_password_hash()
        try:
            await asyncio.to_thread(self.password_hasher.verify, password_hash, command.password)
        except VerificationError:
            await self._fail_login(limiter_keys)
        if account is None or account.status != "active":
            await self._fail_login(limiter_keys)
        now = int(utc_now().timestamp())
        account.last_login_at = now
        account.last_login_ip = command.client_ip
        account.updated_at = now
        await self.db.commit()
        for limiter, key in zip(self.login_limiters, limiter_keys, strict=True):
            await limiter.reset(key)
        return await issue_tokens(account.id)

    async def reject_login(self, email: str, client_ip: str) -> NoReturn:
        normalized_email = self._normalize_login_email(email)
        await self._fail_login((f"email:{normalized_email}", f"ip:{client_ip}"))

    async def _fail_login(self, keys: tuple[str, str]) -> NoReturn:
        for limiter, key in zip(self.login_limiters, keys, strict=True):
            await limiter.increment(key)
        raise AuthenticationFailedError("Invalid email or password")

    async def authenticate_access(self, token: str) -> Principal:
        claims = PassportService().verify(token)
        if await is_access_token_revoked(claims):
            raise AuthenticationFailedError("Invalid access token")
        account_id = claims.get("sub")
        if not isinstance(account_id, str):
            raise AuthenticationFailedError("Invalid access token")
        account = await self.db.get(Account, account_id)
        if account is None or account.status != "active":
            raise AuthenticationFailedError("Invalid access token")
        return Principal(
            account_id=account.id,
            email=account.email,
            name=account.name,
            role=AccountRole(account.role),
        )

    async def refresh(self, refresh_token: str | None) -> AuthTokens:
        if not refresh_token:
            raise AuthenticationFailedError("Invalid refresh token")
        session = await consume_refresh_token(refresh_token)
        if session is None:
            raise AuthenticationFailedError("Invalid refresh token")
        account = await self.db.get(Account, session.account_id)
        if account is None or account.status != "active":
            raise AuthenticationFailedError("Invalid refresh token")
        await revoke_access_token_id(session.access_jti)
        return await issue_tokens(account.id)

    async def provision_member(self, command: ProvisionMemberCommand) -> str:
        if command.role is AccountRole.OWNER:
            raise InvalidSetupInput("The owner account can only be created during setup")
        if await self.setup_at() is None:
            raise SetupRequiredError
        email, normalized_email, name = self._validate_identity(command.email, command.name)
        self._validate_password(command.password)
        if await self.db.scalar(select(Account.id).where(Account.normalized_email == normalized_email)):
            raise DuplicateAccountError
        now = int(utc_now().timestamp())
        account = self._new_account(
            email=email,
            normalized_email=normalized_email,
            name=name,
            password=command.password,
            language="en-US",
            role=command.role,
            now=now,
        )
        self.db.add(account)
        try:
            await self.db.commit()
        except IntegrityError as error:
            await self.db.rollback()
            raise DuplicateAccountError from error
        return account.id

    def _new_account(
        self,
        *,
        email: str,
        normalized_email: str,
        name: str,
        password: str,
        language: str,
        role: AccountRole,
        now: int,
    ) -> Account:
        return Account(
            id=str(uuid4()),
            email=email,
            normalized_email=normalized_email,
            name=name,
            password_hash=self.password_hasher.hash(password),
            role=role.value,
            status="active",
            interface_language=language,
            interface_theme="light",
            timezone="UTC",
            avatar_url=None,
            initialized_at=now,
            last_login_at=None,
            last_login_ip=None,
            created_at=now,
            updated_at=now,
        )

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
