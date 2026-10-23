# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy
import hashlib
import json
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import Session

from app.models import Record, SessionRecord, User


class DomainError(Exception):
    def __init__(self, code: str, detail: str, status: int = 400):
        self.code, self.detail, self.status = code, detail, status


def revision(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.data_dir / 'wizard.sqlite'}",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(self.engine, "connect")
        def configure(connection, _):
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=30000")

        from app.migrate import upgrade

        upgrade(self.engine)

    @contextmanager
    def session(self):
        with Session(self.engine) as session, session.begin():
            yield session

    def create_user(self, username: str, password: str, is_admin: bool = False):
        if not username.strip() or len(password) < 12:
            raise ValueError("A username and password of at least 12 characters are required")
        with self.session() as s:
            if s.get(User, username):
                raise ValueError("User already exists")
            s.add(
                User(
                    username=username,
                    password_hash=PasswordHasher().hash(password),
                    is_admin=int(is_admin),
                )
            )

    def login(self, username: str, password: str):
        with self.session() as s:
            user = s.get(User, username)
            try:
                if not user or not PasswordHasher().verify(user.password_hash, password):
                    raise DomainError("login_failed", "Invalid credentials", 401)
            except VerificationError as e:
                raise DomainError("login_failed", "Invalid credentials", 401) from e
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            s.add(
                SessionRecord(
                    id=revision(token), username=username, csrf=csrf, expires=time.time() + 8 * 3600
                )
            )
            return token, csrf

    def actor(self, token: str):
        with self.session() as s:
            record = s.get(SessionRecord, revision(token))
            if not record or record.expires <= time.time():
                raise DomainError("session-expired", "Sign in to the wizard", 401)
            return {"username": record.username, "csrf": record.csrf}

    def logout(self, token: str):
        with self.session() as s:
            record = s.get(SessionRecord, revision(token))
            if record:
                s.delete(record)

    def save(self, kind: str, key: str, owner: str, value: dict, expected: str | None = None):
        value = copy.deepcopy(value)
        new_revision = revision(value)
        with self.session() as s:
            existing = s.get(Record, key)
            if existing:
                if existing.owner != owner or existing.kind != kind:
                    raise DomainError("not_found", "Not found", 404)
                if expected is not None:
                    result = s.execute(
                        update(Record)
                        .where(Record.id == key, Record.revision == expected)
                        .values(data=value, revision=new_revision)
                    )
                    if result.rowcount != 1:
                        raise DomainError("stale-revision", "Reload the changed draft", 409)
                else:
                    existing.data, existing.revision = value, new_revision
            else:
                if expected is not None:
                    raise DomainError("not_found", "Not found", 404)
                s.add(Record(id=key, kind=kind, owner=owner, data=value, revision=new_revision))
        return {**value, "revision": new_revision}

    def get(self, kind: str, key: str, owner: str):
        with self.session() as s:
            record = s.get(Record, key)
            if not record or record.kind != kind or record.owner != owner:
                raise DomainError("not_found", "Not found", 404)
            return {**copy.deepcopy(record.data), "revision": record.revision}

    def list(self, kind: str, owner: str):
        with self.session() as s:
            return [
                {**copy.deepcopy(r.data), "revision": r.revision}
                for r in s.scalars(select(Record).where(Record.kind == kind, Record.owner == owner))
            ]
