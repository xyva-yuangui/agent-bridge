"""SQLite persistence for Agent Bridge v2."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Iterable, Optional


@dataclass(frozen=True)
class IntegrityReport:
    """The outcome of SQLite's integrity check."""

    ok: bool
    message: str


class Store:
    """A single SQLite connection with schema migration support."""

    def __init__(self, path: Path, connection: sqlite3.Connection) -> None:
        self.path = path
        self.connection = connection

    @classmethod
    def open(cls, path: Path) -> "Store":
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 5.0
        while True:
            connection = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA busy_timeout=5000")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
                store = cls(path, connection)
                store.apply_migrations()
                return store
            except sqlite3.OperationalError as error:
                connection.close()
                if "locked" not in str(error).lower() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
            except Exception:
                connection.close()
                raise

    def close(self) -> None:
        self.connection.close()

    def scalar(self, sql: str, parameters: Iterable[object] = ()) -> object:
        row = self.connection.execute(sql, tuple(parameters)).fetchone()
        return None if row is None else row[0]

    @contextmanager
    def transaction(self, immediate: bool = False) -> Generator[sqlite3.Connection, None, None]:
        self.connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield self.connection
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def integrity_report(self) -> IntegrityReport:
        message = str(self.scalar("PRAGMA integrity_check"))
        return IntegrityReport(ok=message.lower() == "ok", message=message)

    def apply_migrations(self) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            has_migration_table = self._migration_table_exists()
            for version, source in self._migration_sources():
                checksum = hashlib.sha256(source.encode("utf-8")).hexdigest()
                applied = self.connection.execute(
                    "SELECT checksum FROM schema_migrations WHERE version = ?", (version,)
                ).fetchone() if has_migration_table else None
                if applied is not None:
                    if applied[0] != checksum:
                        raise RuntimeError("migration checksum mismatch for version {0}".format(version))
                    continue
                if version > 1 and has_migration_table:
                    self._backup_before_migration(version)
                _execute_sql_script(self.connection, source)
                self.connection.execute(
                    "INSERT INTO schema_migrations(version, checksum, applied_at) VALUES (?, ?, ?)",
                    (version, checksum, _utc_now()),
                )
                has_migration_table = True
            self.connection.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def _migration_table_exists(self) -> bool:
        return bool(self.scalar(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ))

    def _migration_sources(self) -> Iterable[tuple[int, str]]:
        migration_directory = Path(__file__).with_name("migrations")
        for migration_path in sorted(migration_directory.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            version = int(migration_path.name.split("_", 1)[0])
            yield version, migration_path.read_text(encoding="utf-8")

    def _backup_before_migration(self, version: int) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = self.path.with_name(
            "{0}.before-v{1}.{2}.bak".format(self.path.name, version, timestamp)
        )
        source_connection = sqlite3.connect(str(self.path), timeout=5.0)
        backup_connection = sqlite3.connect(str(backup_path))
        try:
            source_connection.backup(backup_connection)
        finally:
            source_connection.close()
            backup_connection.close()
        return backup_path


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _execute_sql_script(connection: sqlite3.Connection, source: str) -> None:
    """Execute complete SQL statements without ending the caller's transaction."""
    statement = ""
    for line in source.splitlines(True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("migration contains an incomplete SQL statement")
