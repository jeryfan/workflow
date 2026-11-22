from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    and_,
    create_engine,
    event,
    func,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine

from api.db.migrations import upgrade
from api.models.entities import (
    Actor,
    AppId,
    AppRecord,
    AppSite,
    ContainerEvent,
    CopyAppCommand,
    CreateAppCommand,
    Draft,
    DraftConflict,
    DraftHash,
    EnvironmentVariablePatch,
    ExecutionEvent,
    GraphDocument,
    NodeExecutionRecord,
    NodeFinished,
    NodeStarted,
    PublishDraft,
    PublishedWorkflow,
    RunFailed,
    RunIdentity,
    RunPage,
    RunPartiallySucceeded,
    RunRecord,
    RunSnapshot,
    RunStopped,
    RunSucceeded,
    SaveDraft,
    TerminalStateConflict,
    UpdateAppCommand,
    UploadedFile,
    WorkflowFinished,
    WorkflowId,
    WorkflowStarted,
    WorkflowTool,
    WorkflowTrigger,
    generate_draft_hash,
    merge_environment_variables,
)


class AlreadySetupError(RuntimeError):
    pass


class SetupRequiredError(RuntimeError):
    pass


class DuplicateAccountError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SetupAccount:
    id: str
    email: str
    normalized_email: str
    name: str
    password_hash: str
    language: str
    created_at: int


@dataclass(frozen=True, slots=True)
class LoginAccount:
    id: str
    password_hash: str
    status: str
    role: str | None


@dataclass(frozen=True, slots=True)
class SessionPrincipal:
    account_id: str
    email: str
    name: str
    role: str
    session_id: str
    csrf_token_hash: str


@dataclass(frozen=True, slots=True)
class NewSession:
    id: str
    account_id: str
    access_token_hash: str
    refresh_token_hash: str
    csrf_token_hash: str
    access_expires_at: int
    refresh_expires_at: int
    created_at: int


@dataclass(frozen=True, slots=True)
class SessionRotation:
    old_refresh_token_hash: str
    access_token_hash: str
    refresh_token_hash: str
    csrf_token_hash: str
    access_expires_at: int
    refresh_expires_at: int
    refreshed_at: int


def upsert_insert(connection: Connection, table: Table):
    if connection.dialect.name == "postgresql":
        return postgresql_insert(table)
    return sqlite_insert(table)


class Store:
    def __init__(self, database_url: str) -> None:
        options = {"connect_args": {"check_same_thread": False}} if database_url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(database_url, pool_pre_ping=True, **options)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", self._configure_connection)
        upgrade(database_url)
        self.metadata = MetaData()
        self.apps = Table(
            "apps",
            self.metadata,
            Column("id", Text, primary_key=True),
            Column("name", Text, nullable=False),
            Column("mode", Text, nullable=False),
            Column("created_at", Integer, nullable=False),
            Column("maintainer_id", Text, ForeignKey("accounts.id")),
            Column("created_by_id", Text, ForeignKey("accounts.id")),
            Column("updated_by_id", Text, ForeignKey("accounts.id")),
            Column("published_workflow_id", Text, ForeignKey("workflow_versions.id")),
            Column("description", Text, nullable=False, default=""),
            Column("icon", Text),
            Column("icon_type", Text),
            Column("icon_background", Text),
            Column("use_icon_as_answer_icon", Integer, nullable=False, default=0),
            Column("updated_at", Integer),
        )
        self.app_sites = Table(
            "app_sites",
            self.metadata,
            Column("app_id", Text, ForeignKey("apps.id"), primary_key=True),
            Column("access_token", Text, unique=True, nullable=False),
            Column("enable_site", Integer, nullable=False),
            Column("created_at", Integer, nullable=False),
            Column("updated_at", Integer, nullable=False),
        )
        self.workflow_tools = Table(
            "workflow_tools",
            self.metadata,
            Column("id", Text, primary_key=True),
            Column("app_id", Text, ForeignKey("apps.id"), unique=True, nullable=False),
            Column("published_workflow_id", Text, nullable=False),
            Column("name", Text, nullable=False),
            Column("label", Text, nullable=False),
            Column("description", Text, nullable=False),
            Column("icon_json", Text, nullable=False),
            Column("parameters_json", Text, nullable=False),
            Column("labels_json", Text, nullable=False),
            Column("privacy_policy", Text, nullable=False),
            Column("created_at", Integer, nullable=False),
            Column("updated_at", Integer, nullable=False),
            UniqueConstraint("name"),
        )
        self.workflow_triggers = Table(
            "workflow_triggers",
            self.metadata,
            Column("id", Text, primary_key=True),
            Column("app_id", Text, ForeignKey("apps.id"), nullable=False),
            Column("workflow_id", Text, nullable=False),
            Column("node_id", Text, nullable=False),
            Column("trigger_type", Text, nullable=False),
            Column("status", Text, nullable=False),
            Column("webhook_id", Text, unique=True),
            Column("config_json", Text, nullable=False),
            Column("created_by_json", Text, nullable=False),
            Column("next_run_at", Integer),
            Column("last_run_key", Text),
            Column("created_at", Integer, nullable=False),
            Column("updated_at", Integer, nullable=False),
            UniqueConstraint("app_id", "node_id"),
        )
        self.uploaded_files = Table(
            "uploaded_files",
            self.metadata,
            Column("id", Text, primary_key=True),
            Column("app_id", Text, ForeignKey("apps.id")),
            Column("name", Text, nullable=False),
            Column("extension", Text, nullable=False),
            Column("mime_type", Text, nullable=False),
            Column("size", Integer, nullable=False),
            Column("storage_key", Text, unique=True, nullable=False),
            Column("source_url", Text),
            Column("created_by", Text, nullable=False),
            Column("created_at", Integer, nullable=False),
        )
        self.workflow_versions = Table(
            "workflow_versions",
            self.metadata,
            Column("id", Text, primary_key=True),
            Column("app_id", Text, ForeignKey("apps.id"), nullable=False),
            Column("version_number", Integer, nullable=False),
            Column("graph_json", Text, nullable=False),
            Column("features_json", Text, nullable=False),
            Column("environment_variables_json", Text, nullable=False),
            Column("conversation_variables_json", Text, nullable=False),
            Column("hash", Text, nullable=False),
            Column("marked_name", Text, nullable=False),
            Column("marked_comment", Text, nullable=False),
            Column("created_at", Integer, nullable=False),
            Column("created_by_json", Text, nullable=False),
            UniqueConstraint("app_id", "version_number"),
        )
        self.drafts = Table(
            "workflow_drafts",
            self.metadata,
            Column("app_id", Text, ForeignKey("apps.id"), primary_key=True),
            Column("workflow_id", Text, unique=True, nullable=False),
            Column("graph_json", Text, nullable=False),
            Column("features_json", Text, nullable=False),
            Column("environment_variables_json", Text, nullable=False),
            Column("conversation_variables_json", Text, nullable=False),
            Column("hash", Text, nullable=False),
            Column("created_at", Integer, nullable=False),
            Column("updated_at", Integer, nullable=False),
            Column("created_by_json", Text, nullable=False),
            Column("updated_by_json", Text, nullable=False),
        )
        self.runs = Table(
            "workflow_runs",
            self.metadata,
            Column("id", Text, primary_key=True),
            Column("task_id", Text, unique=True, nullable=False),
            Column("app_id", Text, ForeignKey("apps.id"), nullable=False),
            Column("workflow_id", Text, nullable=False),
            Column("draft_hash", Text, nullable=False),
            Column("graph_snapshot_json", Text, nullable=False),
            Column("inputs_json", Text, nullable=False),
            Column("status", Text, nullable=False),
            Column("outputs_json", Text, nullable=False),
            Column("error", Text),
            Column("elapsed_time", Float, nullable=False),
            Column("total_tokens", Integer, nullable=False),
            Column("total_steps", Integer, nullable=False),
            Column("exceptions_count", Integer, nullable=False),
            Column("created_by_json", Text, nullable=False),
            Column("created_at", Integer, nullable=False),
            Column("finished_at", Integer),
            Column("root_node_id", Text),
            Column("call_depth", Integer, nullable=False, default=0),
            Column("call_stack_json", Text, nullable=False, default="[]"),
        )
        self.node_executions = Table(
            "node_executions",
            self.metadata,
            Column("id", Text, primary_key=True),
            Column("run_id", Text, ForeignKey("workflow_runs.id"), nullable=False),
            Column("sequence", Integer, nullable=False),
            Column("node_id", Text, nullable=False),
            Column("node_type", Text, nullable=False),
            Column("title", Text, nullable=False),
            Column("index", Integer, nullable=False),
            Column("predecessor_node_id", Text),
            Column("inputs_json", Text, nullable=False),
            Column("process_data_json", Text, nullable=False),
            Column("outputs_json", Text, nullable=False),
            Column("status", Text, nullable=False),
            Column("error", Text),
            Column("elapsed_time", Float, nullable=False),
            Column("created_at", Integer, nullable=False),
            Column("finished_at", Integer),
            UniqueConstraint("run_id", "sequence"),
        )
        self.accounts = Table(
            "accounts",
            self.metadata,
            Column("id", Text, primary_key=True),
            Column("email", Text, nullable=False),
            Column("normalized_email", Text, unique=True, nullable=False),
            Column("name", Text, nullable=False),
            Column("password_hash", Text, nullable=False),
            Column("role", Text, nullable=False),
            Column("status", Text, nullable=False),
            Column("interface_language", Text, nullable=False),
            Column("interface_theme", Text, nullable=False),
            Column("timezone", Text, nullable=False),
            Column("avatar_url", Text),
            Column("initialized_at", Integer),
            Column("last_login_at", Integer),
            Column("last_login_ip", Text),
            Column("created_at", Integer, nullable=False),
            Column("updated_at", Integer, nullable=False),
        )
        self.installation_state = Table(
            "installation_state",
            self.metadata,
            Column("id", Integer, primary_key=True),
            Column("setup_at", Integer, nullable=False),
        )
        self.auth_sessions = Table(
            "auth_sessions",
            self.metadata,
            Column("id", Text, primary_key=True),
            Column("account_id", Text, ForeignKey("accounts.id"), nullable=False),
            Column("access_token_hash", Text, unique=True, nullable=False),
            Column("refresh_token_hash", Text, unique=True, nullable=False),
            Column("csrf_token_hash", Text, nullable=False),
            Column("access_expires_at", Integer, nullable=False),
            Column("refresh_expires_at", Integer, nullable=False),
            Column("created_at", Integer, nullable=False),
            Column("refreshed_at", Integer),
            Column("revoked_at", Integer),
        )
        self.auth_throttles = Table(
            "auth_throttles",
            self.metadata,
            Column("action", Text, primary_key=True),
            Column("subject", Text, primary_key=True),
            Column("failure_count", Integer, nullable=False),
            Column("window_started_at", Integer, nullable=False),
            Column("locked_until", Integer),
        )

    @staticmethod
    def _configure_connection(_dbapi_connection, _connection_record) -> None:
        cursor = _dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    def setup_at(self) -> int | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.installation_state.c.setup_at)).first()
        return row.setup_at if row is not None else None

    def setup_owner(self, account: SetupAccount) -> None:
        with self.engine.begin() as connection:
            if self._has_identity_state(connection):
                raise AlreadySetupError
            claimed = connection.execute(
                upsert_insert(connection, self.installation_state)
                .values(id=1, setup_at=account.created_at)
                .on_conflict_do_nothing()
                .returning(self.installation_state.c.id)
            ).scalar_one_or_none()
            if claimed is None:
                raise AlreadySetupError
            self._insert_account(connection, account)
            connection.execute(
                self.apps.update().values(
                    maintainer_id=account.id,
                    created_by_id=account.id,
                    updated_by_id=account.id,
                )
            )

    def _has_identity_state(self, connection: Connection) -> bool:
        return any(
            connection.execute(select(table.c[0])).first() is not None
            for table in (self.installation_state, self.accounts)
        )

    def _insert_account(self, connection: Connection, account: SetupAccount, role: str = "owner") -> None:
        connection.execute(
            self.accounts.insert().values(
                id=account.id,
                email=account.email,
                normalized_email=account.normalized_email,
                name=account.name,
                password_hash=account.password_hash,
                role=role,
                status="active",
                interface_language=account.language,
                interface_theme="light",
                timezone="UTC",
                avatar_url=None,
                initialized_at=account.created_at,
                last_login_at=None,
                last_login_ip=None,
                created_at=account.created_at,
                updated_at=account.created_at,
            )
        )

    def create_member(self, account: SetupAccount, role: str) -> None:
        with self.engine.begin() as connection:
            initialized = connection.execute(select(self.installation_state.c.id)).first()
            if initialized is None:
                raise SetupRequiredError
            duplicate = connection.execute(
                select(self.accounts.c.id).where(self.accounts.c.normalized_email == account.normalized_email)
            ).first()
            if duplicate is not None:
                raise DuplicateAccountError
            self._insert_account(connection, account, role)

    def account_for_login(self, normalized_email: str) -> LoginAccount | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(
                    self.accounts.c.id,
                    self.accounts.c.password_hash,
                    self.accounts.c.status,
                    self.accounts.c.role,
                ).where(self.accounts.c.normalized_email == normalized_email)
            ).first()
        if row is None:
            return None
        return LoginAccount(id=row.id, password_hash=row.password_hash, status=row.status, role=row.role)

    def login_is_throttled(self, subjects: tuple[str, str], now: int) -> bool:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.auth_throttles.c.subject).where(
                    self.auth_throttles.c.action == "login",
                    self.auth_throttles.c.subject.in_(subjects),
                    self.auth_throttles.c.locked_until > now,
                )
            ).first()
        return row is not None

    def record_login_failures(
        self,
        subjects: tuple[str, str],
        now: int,
        limit: int,
        window: int,
    ) -> None:
        with self.engine.begin() as connection:
            for subject in subjects:
                current = connection.execute(
                    select(
                        self.auth_throttles.c.failure_count,
                        self.auth_throttles.c.window_started_at,
                        self.auth_throttles.c.locked_until,
                    ).where(
                        self.auth_throttles.c.action == "login",
                        self.auth_throttles.c.subject == subject,
                    )
                ).first()
                if current is not None and current.locked_until is not None and current.locked_until > now:
                    continue
                expired = current is not None and (
                    now - current.window_started_at >= window
                    or (current.locked_until is not None and current.locked_until <= now)
                )
                failure_count = 1 if current is None or expired else min(limit, current.failure_count + 1)
                values = {
                    "failure_count": failure_count,
                    "window_started_at": now if current is None or expired else current.window_started_at,
                    "locked_until": now + window if failure_count >= limit else None,
                }
                if current is None:
                    connection.execute(self.auth_throttles.insert().values(action="login", subject=subject, **values))
                else:
                    connection.execute(
                        self.auth_throttles.update()
                        .where(
                            self.auth_throttles.c.action == "login",
                            self.auth_throttles.c.subject == subject,
                        )
                        .values(**values)
                    )

    def create_session(self, session: NewSession, client_ip: str, subjects: tuple[str, str]) -> bool:
        with self.engine.begin() as connection:
            active_account = self._active_account_statement(session.account_id)
            if connection.execute(active_account).first() is None:
                return False
            connection.execute(
                self.auth_sessions.insert().values(
                    id=session.id,
                    account_id=session.account_id,
                    access_token_hash=session.access_token_hash,
                    refresh_token_hash=session.refresh_token_hash,
                    csrf_token_hash=session.csrf_token_hash,
                    access_expires_at=session.access_expires_at,
                    refresh_expires_at=session.refresh_expires_at,
                    created_at=session.created_at,
                    refreshed_at=None,
                    revoked_at=None,
                )
            )
            connection.execute(
                self.accounts.update()
                .where(self.accounts.c.id == session.account_id)
                .values(last_login_at=session.created_at, last_login_ip=client_ip)
            )
            connection.execute(
                self.auth_throttles.delete().where(
                    self.auth_throttles.c.action == "login",
                    self.auth_throttles.c.subject.in_(subjects),
                )
            )
        return True

    def authenticate_access(self, access_token_hash: str, now: int) -> SessionPrincipal | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(
                    self.accounts.c.id,
                    self.accounts.c.email,
                    self.accounts.c.name,
                    self.accounts.c.role,
                    self.auth_sessions.c.id.label("session_id"),
                    self.auth_sessions.c.csrf_token_hash,
                )
                .select_from(self.auth_sessions)
                .join(self.accounts, self.accounts.c.id == self.auth_sessions.c.account_id)
                .where(
                    self.auth_sessions.c.access_token_hash == access_token_hash,
                    self.auth_sessions.c.revoked_at.is_(None),
                    self.auth_sessions.c.access_expires_at > now,
                    self.accounts.c.status == "active",
                )
            ).first()
        if row is None:
            return None
        return SessionPrincipal(
            account_id=row.id,
            email=row.email,
            name=row.name,
            role=row.role,
            session_id=row.session_id,
            csrf_token_hash=row.csrf_token_hash,
        )

    def rotate_session(self, rotation: SessionRotation) -> bool:
        active_account = select(self.accounts.c.id).where(
            self.accounts.c.id == self.auth_sessions.c.account_id,
            self.accounts.c.status == "active",
        )
        with self.engine.begin() as connection:
            result = connection.execute(
                self.auth_sessions.update()
                .where(
                    self.auth_sessions.c.refresh_token_hash == rotation.old_refresh_token_hash,
                    self.auth_sessions.c.revoked_at.is_(None),
                    self.auth_sessions.c.refresh_expires_at > rotation.refreshed_at,
                    self.auth_sessions.c.account_id.in_(active_account),
                )
                .values(
                    access_token_hash=rotation.access_token_hash,
                    refresh_token_hash=rotation.refresh_token_hash,
                    csrf_token_hash=rotation.csrf_token_hash,
                    access_expires_at=rotation.access_expires_at,
                    refresh_expires_at=rotation.refresh_expires_at,
                    refreshed_at=rotation.refreshed_at,
                )
            )
            return result.rowcount == 1

    def revoke_session(self, session_id: str, now: int) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                self.auth_sessions.update()
                .where(self.auth_sessions.c.id == session_id, self.auth_sessions.c.revoked_at.is_(None))
                .values(revoked_at=now)
            )

    def _active_account_statement(self, account_id: str):
        return select(self.accounts.c.id).where(
            self.accounts.c.id == account_id,
            self.accounts.c.status == "active",
        )

    def close(self) -> None:
        self.engine.dispose()

    def ensure_app(self, app_id: str) -> None:
        with self.engine.begin() as connection:
            statement = upsert_insert(connection, self.apps).values(
                id=app_id,
                name="Workflow",
                mode="workflow",
                created_at=int(time.time()),
            )
            connection.execute(statement.on_conflict_do_nothing())

    @staticmethod
    def _app_record(row) -> AppRecord:
        return AppRecord(
            id=row.id,
            name=row.name,
            mode=row.mode,
            created_at=row.created_at,
            updated_at=row.updated_at if row.updated_at is not None else row.created_at,
            maintainer_id=row.maintainer_id,
            created_by_id=row.created_by_id,
            updated_by_id=row.updated_by_id,
            published_workflow_id=row.published_workflow_id,
            author_name=row.author_name,
            description=row.description or "",
            icon=row.icon,
            icon_type=row.icon_type,
            icon_background=row.icon_background,
            use_icon_as_answer_icon=bool(row.use_icon_as_answer_icon),
        )

    def _read_app_record(self, connection: Connection, app_id: str) -> AppRecord | None:
        row = connection.execute(
            select(self.apps, self.accounts.c.name.label("author_name"))
            .outerjoin(self.accounts, self.accounts.c.id == self.apps.c.created_by_id)
            .where(self.apps.c.id == app_id)
        ).first()
        return self._app_record(row) if row is not None else None

    def get_app_record(self, app_id: str) -> AppRecord | None:
        with self.engine.connect() as connection:
            return self._read_app_record(connection, app_id)

    def list_app_records(
        self,
        *,
        name: str | None = None,
        mode: str | None = None,
        sort_by: str = "last_modified",
        creator_ids: list[str] | None = None,
    ) -> list[AppRecord]:
        statement = select(self.apps, self.accounts.c.name.label("author_name")).outerjoin(
            self.accounts, self.accounts.c.id == self.apps.c.created_by_id
        )
        if name:
            statement = statement.where(func.lower(self.apps.c.name).contains(name.lower()))
        if mode and mode != "all":
            statement = statement.where(self.apps.c.mode == mode)
        if creator_ids:
            statement = statement.where(self.apps.c.created_by_id.in_(creator_ids))
        if sort_by == "earliest_created":
            statement = statement.order_by(self.apps.c.created_at.asc(), self.apps.c.id.asc())
        elif sort_by == "recently_created":
            statement = statement.order_by(self.apps.c.created_at.desc(), self.apps.c.id.desc())
        else:
            statement = statement.order_by(self.apps.c.updated_at.desc(), self.apps.c.id.desc())
        with self.engine.connect() as connection:
            return [self._app_record(row) for row in connection.execute(statement).all()]

    def create_app(self, command: CreateAppCommand) -> AppRecord:
        app_id = str(uuid4())
        now = int(time.time())
        with self.engine.begin() as connection:
            connection.execute(
                self.apps.insert().values(
                    id=app_id,
                    name=command.name,
                    mode=command.mode,
                    description=command.description,
                    icon=command.icon,
                    icon_type=command.icon_type,
                    icon_background=command.icon_background,
                    use_icon_as_answer_icon=0,
                    created_at=now,
                    updated_at=now,
                    maintainer_id=command.actor.id,
                    created_by_id=command.actor.id,
                    updated_by_id=command.actor.id,
                )
            )
            record = self._read_app_record(connection, app_id)
            if record is None:
                raise RuntimeError("Created app could not be read")
            return record

    def update_app(self, command: UpdateAppCommand) -> AppRecord | None:
        values = {}
        for field in (
            "name",
            "description",
            "icon",
            "icon_type",
            "icon_background",
            "use_icon_as_answer_icon",
        ):
            value = getattr(command, field)
            if value is not None:
                values[field] = int(value) if field == "use_icon_as_answer_icon" else value
        with self.engine.begin() as connection:
            if self._read_app_record(connection, command.app_id) is None:
                return None
            if values:
                connection.execute(
                    self.apps.update()
                    .where(self.apps.c.id == command.app_id)
                    .values(
                        **values,
                        updated_at=int(time.time()),
                        updated_by_id=command.actor.id,
                    )
                )
            return self._read_app_record(connection, command.app_id)

    def copy_app(self, command: CopyAppCommand) -> AppRecord | None:
        now = int(time.time())
        app_id = str(uuid4())
        with self.engine.begin() as connection:
            source = self._read_app_record(connection, command.source_app_id)
            if source is None:
                return None
            connection.execute(
                self.apps.insert().values(
                    id=app_id,
                    name=command.name or f"{source.name} copy",
                    mode=source.mode,
                    description=command.description if command.description is not None else source.description,
                    icon=command.icon if command.icon is not None else source.icon,
                    icon_type=command.icon_type if command.icon_type is not None else source.icon_type,
                    icon_background=command.icon_background
                    if command.icon_background is not None
                    else source.icon_background,
                    use_icon_as_answer_icon=int(source.use_icon_as_answer_icon),
                    created_at=now,
                    updated_at=now,
                    maintainer_id=command.actor.id,
                    created_by_id=command.actor.id,
                    updated_by_id=command.actor.id,
                )
            )
            draft = self._read_draft(connection, command.source_app_id)
            if draft is not None:
                actor_json = self._json(asdict(command.actor))
                connection.execute(
                    self.drafts.insert().values(
                        app_id=app_id,
                        workflow_id=str(uuid4()),
                        graph_json=self._json(draft.graph.to_json_object()),
                        features_json=self._json(draft.features),
                        environment_variables_json=self._json(draft.environment_variables),
                        conversation_variables_json=self._json(draft.conversation_variables),
                        hash=generate_draft_hash(draft.graph),
                        created_at=now,
                        updated_at=now,
                        created_by_json=actor_json,
                        updated_by_json=actor_json,
                    )
                )
            return self._read_app_record(connection, app_id)

    def delete_app(self, app_id: str) -> bool:
        with self.engine.begin() as connection:
            if self._read_app_record(connection, app_id) is None:
                return False
            connection.execute(self.apps.update().where(self.apps.c.id == app_id).values(published_workflow_id=None))
            run_ids = select(self.runs.c.id).where(self.runs.c.app_id == app_id)
            connection.execute(self.node_executions.delete().where(self.node_executions.c.run_id.in_(run_ids)))
            connection.execute(self.runs.delete().where(self.runs.c.app_id == app_id))
            connection.execute(self.workflow_versions.delete().where(self.workflow_versions.c.app_id == app_id))
            connection.execute(self.drafts.delete().where(self.drafts.c.app_id == app_id))
            connection.execute(self.app_sites.delete().where(self.app_sites.c.app_id == app_id))
            connection.execute(self.workflow_tools.delete().where(self.workflow_tools.c.app_id == app_id))
            connection.execute(self.workflow_triggers.delete().where(self.workflow_triggers.c.app_id == app_id))
            connection.execute(self.uploaded_files.delete().where(self.uploaded_files.c.app_id == app_id))
            connection.execute(self.apps.delete().where(self.apps.c.id == app_id))
            return True

    def get_draft(self, app_id: AppId) -> Draft | None:
        with self.engine.connect() as connection:
            return self._read_draft(connection, app_id)

    def save_draft(self, command: SaveDraft) -> Draft:
        if command.environment_variables is not None and command.environment_variable_patch is not None:
            raise ValueError("Environment variable replacement and patch cannot be combined")
        new_hash = generate_draft_hash(command.graph)
        now = int(time.time())
        with self.engine.begin() as connection:
            current = connection.execute(
                select(self.drafts.c.hash, self.drafts.c.environment_variables_json).where(
                    self.drafts.c.app_id == command.app_id
                )
            ).first()
            if current is None:
                if command.expected_hash is not None:
                    raise DraftConflict
                self._insert_draft(connection, command, new_hash, now)
            else:
                if current.hash != command.expected_hash:
                    raise DraftConflict
                environment_variables = tuple(json.loads(current.environment_variables_json))
                if command.environment_variables is not None:
                    environment_variables = command.environment_variables
                elif command.environment_variable_patch is not None:
                    environment_variables = merge_environment_variables(
                        environment_variables, command.environment_variable_patch
                    )
                result = connection.execute(
                    self.drafts.update()
                    .where(self.drafts.c.app_id == command.app_id)
                    .where(self.drafts.c.hash == command.expected_hash)
                    .values(
                        graph_json=self._json(command.graph.to_json_object()),
                        features_json=self._json(command.features),
                        environment_variables_json=self._json(environment_variables),
                        conversation_variables_json=self._json(command.conversation_variables),
                        hash=new_hash,
                        updated_at=now,
                        updated_by_json=self._json(asdict(command.actor)),
                    )
                )
                if result.rowcount != 1:
                    raise DraftConflict
            app_values = {
                "updated_at": now,
                "updated_by_id": connection.execute(
                    select(self.accounts.c.id).where(self.accounts.c.id == command.actor.id)
                ).scalar_one_or_none(),
            }
            if command.app_name is not None:
                app_values["name"] = command.app_name
            connection.execute(self.apps.update().where(self.apps.c.id == command.app_id).values(**app_values))
            draft = self._read_draft(connection, command.app_id)
            if draft is None:
                raise RuntimeError("Saved draft could not be read")
            return draft

    def _insert_draft(
        self,
        connection: Connection,
        command: SaveDraft,
        new_hash: DraftHash,
        now: int,
    ) -> None:
        actor_json = self._json(asdict(command.actor))
        if command.environment_variables is not None:
            environment_variables = command.environment_variables
        elif command.environment_variable_patch is not None:
            environment_variables = merge_environment_variables((), command.environment_variable_patch)
        else:
            environment_variables = ()
        connection.execute(
            self.drafts.insert().values(
                app_id=command.app_id,
                workflow_id=str(uuid4()),
                graph_json=self._json(command.graph.to_json_object()),
                features_json=self._json(command.features),
                environment_variables_json=self._json(environment_variables),
                conversation_variables_json=self._json(command.conversation_variables),
                hash=new_hash,
                created_at=now,
                updated_at=now,
                created_by_json=actor_json,
                updated_by_json=actor_json,
            )
        )

    def publish_draft(self, command: PublishDraft) -> PublishedWorkflow | None:
        with self.engine.begin() as connection:
            draft = self._read_draft(connection, command.app_id)
            if draft is None:
                return None
            version_number = connection.execute(
                select(self.workflow_versions.c.version_number)
                .where(self.workflow_versions.c.app_id == command.app_id)
                .order_by(self.workflow_versions.c.version_number.desc())
                .limit(1)
            ).scalar_one_or_none()
            now = int(time.time())
            workflow_id = str(uuid4())
            connection.execute(
                self.workflow_versions.insert().values(
                    id=workflow_id,
                    app_id=command.app_id,
                    version_number=0 if version_number is None else version_number + 1,
                    graph_json=self._json(draft.graph.to_json_object()),
                    features_json=self._json(draft.features),
                    environment_variables_json=self._json(draft.environment_variables),
                    conversation_variables_json=self._json(draft.conversation_variables),
                    hash=draft.hash,
                    marked_name=command.marked_name,
                    marked_comment=command.marked_comment,
                    created_at=now,
                    created_by_json=self._json(asdict(command.actor)),
                )
            )
            connection.execute(
                self.apps.update()
                .where(self.apps.c.id == command.app_id)
                .values(
                    published_workflow_id=workflow_id,
                    updated_at=now,
                    updated_by_id=connection.execute(
                        select(self.accounts.c.id).where(self.accounts.c.id == command.actor.id)
                    ).scalar_one_or_none(),
                )
            )
            return self._read_published(connection, command.app_id)

    @staticmethod
    def _site(row) -> AppSite | None:
        if row is None:
            return None
        return AppSite(
            app_id=AppId(row.app_id),
            access_token=row.access_token,
            enable_site=bool(row.enable_site),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def get_site(self, app_id: AppId) -> AppSite | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.app_sites).where(self.app_sites.c.app_id == app_id)).first()
        return self._site(row)

    def get_enabled_site(self, access_token: str) -> AppSite | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.app_sites).where(
                    self.app_sites.c.access_token == access_token,
                    self.app_sites.c.enable_site == 1,
                )
            ).first()
        return self._site(row)

    def ensure_site(self, app_id: AppId) -> AppSite:
        now = int(time.time())
        with self.engine.begin() as connection:
            connection.execute(
                upsert_insert(connection, self.app_sites)
                .values(
                    app_id=app_id,
                    access_token=secrets.token_urlsafe(24),
                    enable_site=1,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=[self.app_sites.c.app_id])
            )
            row = connection.execute(select(self.app_sites).where(self.app_sites.c.app_id == app_id)).one()
        site = self._site(row)
        if site is None:
            raise RuntimeError("Created app site could not be read")
        return site

    def set_site_enabled(self, app_id: AppId, enabled: bool) -> AppSite | None:
        now = int(time.time())
        with self.engine.begin() as connection:
            result = connection.execute(
                self.app_sites.update()
                .where(self.app_sites.c.app_id == app_id)
                .values(enable_site=int(enabled), updated_at=now)
            )
            if result.rowcount != 1:
                return None
            row = connection.execute(select(self.app_sites).where(self.app_sites.c.app_id == app_id)).one()
        return self._site(row)

    def reset_site_access_token(self, app_id: AppId) -> AppSite | None:
        now = int(time.time())
        with self.engine.begin() as connection:
            result = connection.execute(
                self.app_sites.update()
                .where(self.app_sites.c.app_id == app_id)
                .values(access_token=secrets.token_urlsafe(24), updated_at=now)
            )
            if result.rowcount != 1:
                return None
            row = connection.execute(select(self.app_sites).where(self.app_sites.c.app_id == app_id)).one()
        return self._site(row)

    def _workflow_tool(self, row) -> WorkflowTool | None:
        if row is None:
            return None
        return WorkflowTool(
            id=row.id,
            app_id=AppId(row.app_id),
            published_workflow_id=row.published_workflow_id,
            name=row.name,
            label=row.label,
            description=row.description,
            icon=json.loads(row.icon_json),
            parameters=tuple(json.loads(row.parameters_json)),
            labels=tuple(json.loads(row.labels_json)),
            privacy_policy=row.privacy_policy,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def list_workflow_tools(self) -> list[WorkflowTool]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.workflow_tools).order_by(self.workflow_tools.c.created_at)).all()
        return [tool for row in rows if (tool := self._workflow_tool(row)) is not None]

    def get_workflow_tool(self, tool_id: str) -> WorkflowTool | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.workflow_tools).where(self.workflow_tools.c.id == tool_id)).first()
        return self._workflow_tool(row)

    def get_workflow_tool_by_app(self, app_id: AppId) -> WorkflowTool | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.workflow_tools).where(self.workflow_tools.c.app_id == app_id)).first()
        return self._workflow_tool(row)

    def create_workflow_tool(self, tool: WorkflowTool) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                self.workflow_tools.insert().values(
                    id=tool.id,
                    app_id=tool.app_id,
                    published_workflow_id=tool.published_workflow_id,
                    name=tool.name,
                    label=tool.label,
                    description=tool.description,
                    icon_json=self._json(tool.icon),
                    parameters_json=self._json(tool.parameters),
                    labels_json=self._json(tool.labels),
                    privacy_policy=tool.privacy_policy,
                    created_at=tool.created_at,
                    updated_at=tool.updated_at,
                )
            )

    def update_workflow_tool(self, tool: WorkflowTool) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                self.workflow_tools.update()
                .where(self.workflow_tools.c.id == tool.id)
                .values(
                    published_workflow_id=tool.published_workflow_id,
                    name=tool.name,
                    label=tool.label,
                    description=tool.description,
                    icon_json=self._json(tool.icon),
                    parameters_json=self._json(tool.parameters),
                    labels_json=self._json(tool.labels),
                    privacy_policy=tool.privacy_policy,
                    updated_at=tool.updated_at,
                )
            )

    def delete_workflow_tool(self, tool_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(self.workflow_tools.delete().where(self.workflow_tools.c.id == tool_id))

    def _trigger(self, row) -> WorkflowTrigger | None:
        if row is None:
            return None
        return WorkflowTrigger(
            id=row.id,
            app_id=AppId(row.app_id),
            workflow_id=row.workflow_id,
            node_id=row.node_id,
            trigger_type=row.trigger_type,
            status=row.status,
            webhook_id=row.webhook_id,
            config=json.loads(row.config_json),
            created_by=Actor(**json.loads(row.created_by_json)),
            next_run_at=row.next_run_at,
            last_run_key=row.last_run_key,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def list_workflow_triggers(self, app_id: AppId, workflow_id: str | None = None) -> list[WorkflowTrigger]:
        conditions = [self.workflow_triggers.c.app_id == app_id]
        if workflow_id is not None:
            conditions.append(self.workflow_triggers.c.workflow_id == workflow_id)
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(self.workflow_triggers).where(*conditions).order_by(self.workflow_triggers.c.created_at)
            ).all()
        return [trigger for row in rows if (trigger := self._trigger(row)) is not None]

    def get_workflow_trigger(self, app_id: AppId, trigger_id: str) -> WorkflowTrigger | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.workflow_triggers).where(
                    self.workflow_triggers.c.app_id == app_id,
                    self.workflow_triggers.c.id == trigger_id,
                )
            ).first()
        return self._trigger(row)

    def get_workflow_trigger_by_node(self, app_id: AppId, node_id: str) -> WorkflowTrigger | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.workflow_triggers).where(
                    self.workflow_triggers.c.app_id == app_id,
                    self.workflow_triggers.c.node_id == node_id,
                )
            ).first()
        return self._trigger(row)

    def get_enabled_webhook(self, webhook_id: str) -> WorkflowTrigger | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.workflow_triggers)
                .join(self.apps, self.apps.c.published_workflow_id == self.workflow_triggers.c.workflow_id)
                .where(
                    self.workflow_triggers.c.webhook_id == webhook_id,
                    self.workflow_triggers.c.trigger_type == "trigger-webhook",
                    self.workflow_triggers.c.status == "enabled",
                )
            ).first()
        return self._trigger(row)

    def get_draft_webhook(self, webhook_id: str) -> WorkflowTrigger | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.workflow_triggers)
                .join(self.drafts, self.drafts.c.app_id == self.workflow_triggers.c.app_id)
                .where(
                    self.workflow_triggers.c.webhook_id == webhook_id,
                    self.workflow_triggers.c.trigger_type == "trigger-webhook",
                )
            ).first()
        return self._trigger(row)

    def ensure_webhook(
        self,
        *,
        app_id: AppId,
        workflow_id: str,
        node_id: str,
        config: dict,
        actor: Actor,
    ) -> WorkflowTrigger:
        now = int(time.time())
        with self.engine.begin() as connection:
            row = connection.execute(
                select(self.workflow_triggers).where(
                    self.workflow_triggers.c.app_id == app_id,
                    self.workflow_triggers.c.node_id == node_id,
                )
            ).first()
            if row is None:
                connection.execute(
                    self.workflow_triggers.insert().values(
                        id=str(uuid4()),
                        app_id=app_id,
                        workflow_id=workflow_id,
                        node_id=node_id,
                        trigger_type="trigger-webhook",
                        status="disabled",
                        webhook_id=secrets.token_urlsafe(24),
                        config_json=self._json(config),
                        created_by_json=self._json(asdict(actor)),
                        next_run_at=None,
                        last_run_key=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                connection.execute(
                    self.workflow_triggers.update()
                    .where(self.workflow_triggers.c.id == row.id)
                    .values(workflow_id=workflow_id, config_json=self._json(config), updated_at=now)
                )
            row = connection.execute(
                select(self.workflow_triggers).where(
                    self.workflow_triggers.c.app_id == app_id,
                    self.workflow_triggers.c.node_id == node_id,
                )
            ).one()
        trigger = self._trigger(row)
        if trigger is None or trigger.webhook_id is None:
            raise RuntimeError("Created webhook trigger could not be read")
        return trigger

    def sync_workflow_triggers(
        self,
        *,
        app_id: AppId,
        workflow_id: str,
        definitions: list[dict],
        actor: Actor,
    ) -> None:
        now = int(time.time())
        with self.engine.begin() as connection:
            rows = connection.execute(
                select(self.workflow_triggers).where(self.workflow_triggers.c.app_id == app_id)
            ).all()
            existing = {row.node_id: row for row in rows}
            node_ids = {definition["node_id"] for definition in definitions}
            for node_id in set(existing) - node_ids:
                connection.execute(
                    self.workflow_triggers.delete().where(self.workflow_triggers.c.id == existing[node_id].id)
                )
            for definition in definitions:
                node_id = definition["node_id"]
                current = existing.get(node_id)
                values = {
                    "workflow_id": workflow_id,
                    "trigger_type": definition["trigger_type"],
                    "config_json": self._json(definition["config"]),
                    "created_by_json": self._json(asdict(actor)),
                    "next_run_at": definition.get("next_run_at"),
                    "updated_at": now,
                }
                if current is not None:
                    is_draft = connection.execute(
                        select(self.drafts.c.app_id).where(self.drafts.c.workflow_id == current.workflow_id)
                    ).first()
                    if is_draft is not None:
                        values["status"] = "enabled"
                    connection.execute(
                        self.workflow_triggers.update()
                        .where(self.workflow_triggers.c.id == current.id)
                        .values(**values)
                    )
                    continue
                connection.execute(
                    self.workflow_triggers.insert().values(
                        id=str(uuid4()),
                        app_id=app_id,
                        node_id=node_id,
                        status="enabled",
                        webhook_id=(
                            secrets.token_urlsafe(24) if definition["trigger_type"] == "trigger-webhook" else None
                        ),
                        created_at=now,
                        **values,
                    )
                )

    def set_workflow_trigger_enabled(self, app_id: AppId, trigger_id: str, enabled: bool) -> WorkflowTrigger | None:
        with self.engine.begin() as connection:
            result = connection.execute(
                self.workflow_triggers.update()
                .where(
                    self.workflow_triggers.c.app_id == app_id,
                    self.workflow_triggers.c.id == trigger_id,
                )
                .values(status="enabled" if enabled else "disabled", updated_at=int(time.time()))
            )
            if result.rowcount != 1:
                return None
            row = connection.execute(
                select(self.workflow_triggers).where(self.workflow_triggers.c.id == trigger_id)
            ).one()
        return self._trigger(row)

    def list_due_schedules(self, now: int) -> list[WorkflowTrigger]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(self.workflow_triggers)
                .join(self.apps, self.apps.c.published_workflow_id == self.workflow_triggers.c.workflow_id)
                .where(
                    self.workflow_triggers.c.trigger_type == "trigger-schedule",
                    self.workflow_triggers.c.status == "enabled",
                    self.workflow_triggers.c.next_run_at.is_not(None),
                    self.workflow_triggers.c.next_run_at <= now,
                )
                .order_by(self.workflow_triggers.c.next_run_at)
                .limit(100)
            ).all()
        return [trigger for row in rows if (trigger := self._trigger(row)) is not None]

    def claim_schedule(
        self,
        *,
        trigger_id: str,
        expected_next_run_at: int,
        run_key: str,
        next_run_at: int,
    ) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(
                self.workflow_triggers.update()
                .where(
                    self.workflow_triggers.c.id == trigger_id,
                    self.workflow_triggers.c.status == "enabled",
                    self.workflow_triggers.c.next_run_at == expected_next_run_at,
                )
                .values(last_run_key=run_key, next_run_at=next_run_at, updated_at=int(time.time()))
            )
            return result.rowcount == 1

    @staticmethod
    def _uploaded_file(row) -> UploadedFile | None:
        if row is None:
            return None
        return UploadedFile(
            id=row.id,
            app_id=AppId(row.app_id) if row.app_id is not None else None,
            name=row.name,
            extension=row.extension,
            mime_type=row.mime_type,
            size=row.size,
            storage_key=row.storage_key,
            source_url=row.source_url,
            created_by=row.created_by,
            created_at=row.created_at,
        )

    def create_uploaded_file(self, file: UploadedFile) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                self.uploaded_files.insert().values(
                    id=file.id,
                    app_id=file.app_id,
                    name=file.name,
                    extension=file.extension,
                    mime_type=file.mime_type,
                    size=file.size,
                    storage_key=file.storage_key,
                    source_url=file.source_url,
                    created_by=file.created_by,
                    created_at=file.created_at,
                )
            )

    def get_uploaded_file(self, file_id: str) -> UploadedFile | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.uploaded_files).where(self.uploaded_files.c.id == file_id)).first()
        return self._uploaded_file(row)

    def list_uploaded_files_for_app(self, app_id: AppId) -> tuple[UploadedFile, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(select(self.uploaded_files).where(self.uploaded_files.c.app_id == app_id)).all()
        return tuple(file for row in rows if (file := self._uploaded_file(row)) is not None)

    def get_published(self, app_id: AppId) -> PublishedWorkflow | None:
        with self.engine.connect() as connection:
            return self._read_published(connection, app_id)

    def get_workflow_version(self, app_id: AppId, workflow_id: str) -> PublishedWorkflow | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.workflow_versions).where(
                    self.workflow_versions.c.app_id == app_id,
                    self.workflow_versions.c.id == workflow_id,
                )
            ).first()
            return self._published_workflow(row) if row is not None else None

    def _read_published(self, connection: Connection, app_id: AppId) -> PublishedWorkflow | None:
        row = connection.execute(
            select(self.workflow_versions)
            .join(self.apps, self.apps.c.published_workflow_id == self.workflow_versions.c.id)
            .where(self.apps.c.id == app_id)
        ).first()
        if row is None:
            return None
        return self._published_workflow(row)

    @staticmethod
    def _published_workflow(row) -> PublishedWorkflow:
        return PublishedWorkflow(
            id=row.id,
            app_id=AppId(row.app_id),
            version_number=row.version_number,
            graph=GraphDocument.parse(json.loads(row.graph_json)),
            features=json.loads(row.features_json),
            environment_variables=tuple(json.loads(row.environment_variables_json)),
            conversation_variables=tuple(json.loads(row.conversation_variables_json)),
            hash=DraftHash(row.hash),
            marked_name=row.marked_name,
            marked_comment=row.marked_comment,
            created_at=datetime.fromtimestamp(row.created_at, UTC),
            created_by=Actor(**json.loads(row.created_by_json)),
        )

    def list_workflow_versions(
        self,
        app_id: AppId,
        page: int,
        limit: int,
        user_id: str | None,
        named_only: bool,
    ) -> tuple[tuple[Draft | PublishedWorkflow, ...], bool]:
        with self.engine.connect() as connection:
            current_id = connection.execute(
                select(self.apps.c.published_workflow_id).where(self.apps.c.id == app_id)
            ).scalar_one_or_none()
            if current_id is None:
                return (), False

            items: list[Draft | PublishedWorkflow] = []
            if not named_only:
                draft_statement = select(self.drafts).where(self.drafts.c.app_id == app_id)
                if user_id is not None:
                    draft_statement = draft_statement.where(
                        func.json_extract(self.drafts.c.created_by_json, "$.id") == user_id
                    )
                draft_row = connection.execute(draft_statement).first()
                if draft_row is not None:
                    items.append(self._draft(draft_row))

            version_statement = (
                select(self.workflow_versions)
                .where(self.workflow_versions.c.app_id == app_id)
                .order_by(
                    self.workflow_versions.c.created_at.desc(),
                    self.workflow_versions.c.version_number.desc(),
                    self.workflow_versions.c.id.desc(),
                )
                .limit(page * limit + 1)
            )
            if user_id is not None:
                version_statement = version_statement.where(
                    func.json_extract(self.workflow_versions.c.created_by_json, "$.id") == user_id
                )
            if named_only:
                version_statement = version_statement.where(self.workflow_versions.c.marked_name != "")
            version_rows = connection.execute(version_statement).all()
            items.extend(self._published_workflow(row) for row in version_rows)

        start = (page - 1) * limit
        selected = items[start : start + limit + 1]
        return tuple(selected[:limit]), len(selected) > limit

    def update_workflow_version(
        self,
        app_id: AppId,
        workflow_id: str,
        marked_name: str | None,
        marked_comment: str | None,
    ) -> PublishedWorkflow | None:
        values = {}
        if marked_name is not None:
            values["marked_name"] = marked_name
        if marked_comment is not None:
            values["marked_comment"] = marked_comment
        with self.engine.begin() as connection:
            result = connection.execute(
                self.workflow_versions.update()
                .where(self.workflow_versions.c.app_id == app_id, self.workflow_versions.c.id == workflow_id)
                .values(**values)
            )
            if result.rowcount != 1:
                return None
            row = connection.execute(
                select(self.workflow_versions).where(
                    self.workflow_versions.c.app_id == app_id,
                    self.workflow_versions.c.id == workflow_id,
                )
            ).first()
        return self._published_workflow(row)

    def restore_workflow_version(self, app_id: AppId, workflow_id: str, actor: Actor) -> Draft | None:
        with self.engine.begin() as connection:
            row = connection.execute(
                select(self.workflow_versions).where(
                    self.workflow_versions.c.app_id == app_id,
                    self.workflow_versions.c.id == workflow_id,
                )
            ).first()
            if row is None:
                return None
            source = self._published_workflow(row)
            now = int(time.time())
            values = {
                "graph_json": row.graph_json,
                "features_json": row.features_json,
                "environment_variables_json": row.environment_variables_json,
                "conversation_variables_json": row.conversation_variables_json,
                "hash": generate_draft_hash(source.graph),
                "updated_at": now,
                "updated_by_json": self._json(asdict(actor)),
            }
            result = connection.execute(self.drafts.update().where(self.drafts.c.app_id == app_id).values(**values))
            if result.rowcount == 0:
                connection.execute(
                    self.drafts.insert().values(
                        app_id=app_id,
                        workflow_id=str(uuid4()),
                        **values,
                        created_at=now,
                        created_by_json=self._json(asdict(actor)),
                    )
                )
            return self._read_draft(connection, app_id)

    def delete_workflow_version(
        self,
        app_id: AppId,
        workflow_id: str,
    ) -> Literal["deleted", "not_found", "current", "draft"]:
        with self.engine.begin() as connection:
            current_id = connection.execute(
                select(self.apps.c.published_workflow_id).where(self.apps.c.id == app_id)
            ).scalar_one_or_none()
            if current_id == workflow_id:
                return "current"
            version_id = connection.execute(
                select(self.workflow_versions.c.id).where(
                    self.workflow_versions.c.app_id == app_id,
                    self.workflow_versions.c.id == workflow_id,
                )
            ).scalar_one_or_none()
            if version_id is None:
                draft_id = connection.execute(
                    select(self.drafts.c.workflow_id).where(
                        self.drafts.c.app_id == app_id,
                        self.drafts.c.workflow_id == workflow_id,
                    )
                ).scalar_one_or_none()
                return "draft" if draft_id is not None else "not_found"
            connection.execute(
                self.workflow_versions.delete().where(
                    self.workflow_versions.c.app_id == app_id,
                    self.workflow_versions.c.id == workflow_id,
                )
            )
            return "deleted"

    def update_environment_variables(
        self,
        app_id: AppId,
        patch: EnvironmentVariablePatch,
        patch_mode: bool,
        actor: Actor,
    ) -> bool:
        with self.engine.begin() as connection:
            row = connection.execute(
                select(self.drafts.c.environment_variables_json).where(self.drafts.c.app_id == app_id)
            ).first()
            if row is None:
                return False
            current = tuple(json.loads(row.environment_variables_json))
            updated = merge_environment_variables(current, patch) if patch_mode else patch.upserts
            result = connection.execute(
                self.drafts.update()
                .where(self.drafts.c.app_id == app_id)
                .values(
                    environment_variables_json=self._json(updated),
                    updated_at=int(time.time()),
                    updated_by_json=self._json(asdict(actor)),
                )
            )
            return result.rowcount == 1

    def _read_draft(self, connection: Connection, app_id: AppId) -> Draft | None:
        row = connection.execute(select(self.drafts).where(self.drafts.c.app_id == app_id)).first()
        return self._draft(row) if row is not None else None

    @staticmethod
    def _draft(row) -> Draft:
        return Draft(
            app_id=AppId(row.app_id),
            workflow_id=WorkflowId(row.workflow_id),
            graph=GraphDocument.parse(json.loads(row.graph_json)),
            features=json.loads(row.features_json),
            environment_variables=tuple(json.loads(row.environment_variables_json)),
            conversation_variables=tuple(json.loads(row.conversation_variables_json)),
            hash=DraftHash(row.hash),
            created_at=datetime.fromtimestamp(row.created_at, UTC),
            updated_at=datetime.fromtimestamp(row.updated_at, UTC),
            created_by=Actor(**json.loads(row.created_by_json)),
            updated_by=Actor(**json.loads(row.updated_by_json)),
        )

    @staticmethod
    def _json(value) -> str:
        return json.dumps(value, ensure_ascii=False)

    def create_run(self, snapshot: RunSnapshot) -> None:
        identity = snapshot.identity
        with self.engine.begin() as connection:
            connection.execute(
                self.runs.insert().values(
                    id=identity.workflow_run_id,
                    task_id=identity.task_id,
                    app_id=identity.app_id,
                    workflow_id=identity.workflow_id,
                    draft_hash=snapshot.draft_hash,
                    graph_snapshot_json=self._json(snapshot.graph.to_json_object()),
                    inputs_json=self._json(snapshot.inputs),
                    status="running",
                    outputs_json="{}",
                    error=None,
                    elapsed_time=0.0,
                    total_tokens=0,
                    total_steps=0,
                    exceptions_count=0,
                    created_by_json=self._json(asdict(snapshot.actor)),
                    created_at=self._timestamp(snapshot.created_at),
                    finished_at=None,
                    root_node_id=snapshot.root_node_id,
                    call_depth=snapshot.call_depth,
                    call_stack_json=self._json(snapshot.call_stack),
                )
            )

    def project_event(self, event: ExecutionEvent) -> None:
        if isinstance(event, WorkflowStarted):
            return
        if isinstance(event, NodeStarted):
            self._project_node_started(event)
            return
        if isinstance(event, NodeFinished):
            self._project_node_finished(event)
            return
        if isinstance(event, ContainerEvent):
            return
        self._project_workflow_finished(event)

    def _project_node_started(self, event: NodeStarted) -> None:
        with self.engine.begin() as connection:
            statement = upsert_insert(connection, self.node_executions).values(
                id=event.execution_id,
                run_id=event.identity.workflow_run_id,
                sequence=event.sequence,
                node_id=event.node_id,
                node_type=event.node_type,
                title=event.title,
                index=event.index,
                predecessor_node_id=event.predecessor_node_id,
                inputs_json="{}",
                process_data_json="{}",
                outputs_json="{}",
                status="running",
                error=None,
                elapsed_time=0.0,
                created_at=self._timestamp(event.created_at),
                finished_at=None,
            )
            connection.execute(statement.on_conflict_do_nothing())

    def _project_node_finished(self, event: NodeFinished) -> None:
        with self.engine.begin() as connection:
            result = connection.execute(
                self.node_executions.update()
                .where(self.node_executions.c.id == event.execution_id)
                .values(
                    sequence=event.sequence,
                    inputs_json=self._json(event.inputs),
                    process_data_json=self._json(event.process_data),
                    outputs_json=self._json(event.outputs),
                    status=event.status,
                    error=event.error,
                    elapsed_time=event.elapsed_time,
                    finished_at=self._timestamp(event.finished_at),
                )
            )
            if result.rowcount != 1:
                raise RuntimeError(f"Node execution was not started: {event.execution_id}")

    def _project_workflow_finished(self, event: WorkflowFinished) -> None:
        status, outputs, error, exceptions_count = self._outcome_values(event)
        with self.engine.begin() as connection:
            result = connection.execute(
                self.runs.update()
                .where(self.runs.c.id == event.identity.workflow_run_id)
                .where(self.runs.c.status == "running")
                .values(
                    status=status,
                    outputs_json=self._json(outputs),
                    error=error,
                    elapsed_time=event.elapsed_time,
                    total_tokens=event.total_tokens,
                    total_steps=event.total_steps,
                    exceptions_count=exceptions_count,
                    finished_at=self._timestamp(event.finished_at),
                )
            )
            if result.rowcount == 1:
                return
            existing = connection.execute(
                select(
                    self.runs.c.status,
                    self.runs.c.outputs_json,
                    self.runs.c.error,
                    self.runs.c.elapsed_time,
                    self.runs.c.total_tokens,
                    self.runs.c.total_steps,
                    self.runs.c.exceptions_count,
                    self.runs.c.finished_at,
                ).where(self.runs.c.id == event.identity.workflow_run_id)
            ).first()
            expected = (
                status,
                self._json(outputs),
                error,
                event.elapsed_time,
                event.total_tokens,
                event.total_steps,
                exceptions_count,
                self._timestamp(event.finished_at),
            )
            if existing is None or tuple(existing) != expected:
                raise TerminalStateConflict

    def list_runs(
        self,
        app_id: AppId,
        limit: int,
        last_id: str | None,
        status: str | None,
    ) -> RunPage:
        with self.engine.connect() as connection:
            statement = select(self.runs).where(self.runs.c.app_id == app_id)
            if status is not None:
                statement = statement.where(self.runs.c.status == status)
            if last_id is not None:
                cursor = connection.execute(
                    select(self.runs.c.created_at, self.runs.c.id).where(
                        self.runs.c.id == last_id, self.runs.c.app_id == app_id
                    )
                ).first()
                if cursor is None:
                    return RunPage(records=(), limit=limit, has_more=False)
                statement = statement.where(
                    or_(
                        self.runs.c.created_at < cursor.created_at,
                        and_(self.runs.c.created_at == cursor.created_at, self.runs.c.id < cursor.id),
                    )
                )
            rows = connection.execute(
                statement.order_by(self.runs.c.created_at.desc(), self.runs.c.id.desc()).limit(limit + 1)
            ).all()
        return RunPage(
            records=tuple(self._run_record(row) for row in rows[:limit]),
            limit=limit,
            has_more=len(rows) > limit,
        )

    def get_run_record(self, app_id: AppId, run_id: str) -> RunRecord | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.runs).where(self.runs.c.id == run_id, self.runs.c.app_id == app_id)
            ).first()
        return self._run_record(row) if row is not None else None

    def list_node_executions(self, app_id: AppId, run_id: str) -> tuple[NodeExecutionRecord, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(self.node_executions)
                .join(self.runs, self.runs.c.id == self.node_executions.c.run_id)
                .where(self.runs.c.id == run_id, self.runs.c.app_id == app_id)
                .order_by(self.node_executions.c.sequence)
            ).all()
        return tuple(self._node_execution_record(row) for row in rows)

    def get_latest_node_execution(
        self,
        app_id: AppId,
        workflow_id: WorkflowId,
        node_id: str,
    ) -> NodeExecutionRecord | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(self.node_executions)
                .join(self.runs, self.runs.c.id == self.node_executions.c.run_id)
                .where(
                    self.runs.c.app_id == app_id,
                    self.runs.c.workflow_id == workflow_id,
                    self.node_executions.c.node_id == node_id,
                )
                .order_by(
                    self.node_executions.c.created_at.desc(),
                    self.node_executions.c.sequence.desc(),
                )
                .limit(1)
            ).first()
        return self._node_execution_record(row) if row is not None else None

    @staticmethod
    def _run_record(row) -> RunRecord:
        return RunRecord(
            identity=RunIdentity(
                app_id=row.app_id,
                workflow_id=row.workflow_id,
                workflow_run_id=row.id,
                task_id=row.task_id,
            ),
            graph=GraphDocument.parse(json.loads(row.graph_snapshot_json)),
            inputs=json.loads(row.inputs_json),
            status=row.status,
            outputs=json.loads(row.outputs_json),
            error=row.error,
            elapsed_time=row.elapsed_time,
            total_tokens=row.total_tokens,
            total_steps=row.total_steps,
            exceptions_count=row.exceptions_count,
            actor=Actor(**json.loads(row.created_by_json)),
            created_at=datetime.fromtimestamp(row.created_at, UTC),
            finished_at=(datetime.fromtimestamp(row.finished_at, UTC) if row.finished_at is not None else None),
            call_depth=row.call_depth,
            call_stack=tuple(json.loads(row.call_stack_json)),
            root_node_id=row.root_node_id,
        )

    def get_run_snapshot(self, run_id: str) -> RunSnapshot | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(self.runs).where(self.runs.c.id == run_id)).first()
        if row is None:
            return None
        record = self._run_record(row)
        return RunSnapshot(
            identity=record.identity,
            graph=record.graph,
            draft_hash=DraftHash(row.draft_hash),
            inputs=record.inputs,
            actor=record.actor,
            created_at=record.created_at,
            call_depth=record.call_depth,
            call_stack=record.call_stack,
            root_node_id=record.root_node_id,
        )

    @staticmethod
    def _node_execution_record(row) -> NodeExecutionRecord:
        return NodeExecutionRecord(
            id=row.id,
            node_id=row.node_id,
            node_type=row.node_type,
            title=row.title,
            index=row.index,
            predecessor_node_id=row.predecessor_node_id,
            inputs=json.loads(row.inputs_json),
            process_data=json.loads(row.process_data_json),
            outputs=json.loads(row.outputs_json),
            status=row.status,
            error=row.error,
            elapsed_time=row.elapsed_time,
            created_at=datetime.fromtimestamp(row.created_at, UTC),
            finished_at=(datetime.fromtimestamp(row.finished_at, UTC) if row.finished_at is not None else None),
        )

    def get_run_identity(self, app_id: AppId, task_id: str) -> RunIdentity | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(
                    self.runs.c.app_id,
                    self.runs.c.workflow_id,
                    self.runs.c.id,
                    self.runs.c.task_id,
                )
                .where(self.runs.c.app_id == app_id)
                .where(self.runs.c.task_id == task_id)
            ).first()
        if row is None:
            return None
        return RunIdentity(app_id=row.app_id, workflow_id=row.workflow_id, workflow_run_id=row.id, task_id=row.task_id)

    def fail_interrupted(self, reason: str) -> int:
        with self.engine.begin() as connection:
            result = connection.execute(
                self.runs.update()
                .where(self.runs.c.status == "running")
                .values(status="failed", error=reason, finished_at=int(time.time()))
            )
            return result.rowcount

    @staticmethod
    def _outcome_values(event: WorkflowFinished) -> tuple[str, dict, str | None, int]:
        outcome = event.outcome
        if isinstance(outcome, RunSucceeded):
            return "succeeded", dict(outcome.outputs), None, outcome.exceptions_count
        if isinstance(outcome, RunPartiallySucceeded):
            return "partial-succeeded", dict(outcome.outputs), None, outcome.exceptions_count
        if isinstance(outcome, RunFailed):
            return "failed", dict(outcome.outputs), outcome.error, outcome.exceptions_count
        if isinstance(outcome, RunStopped):
            return "stopped", dict(outcome.outputs), outcome.reason, 0
        raise TypeError(f"Unknown run outcome: {type(outcome).__name__}")

    @staticmethod
    def _timestamp(value: datetime) -> int:
        return int(value.timestamp())
