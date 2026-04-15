import argparse
import datetime as dt
import fcntl
import sqlite3
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EX_TEMPFAIL = 75


@dataclass(frozen=True)
class QueueConfig:
    db_path: Path
    emergency_dump_path: Path
    lock_file_path: Path
    busy_timeout_ms: int = 30_000


@dataclass(frozen=True)
class MailRecord:
    id: int
    received_at: str
    raw_content: bytes


@dataclass(frozen=True)
class ProcessResult:
    total: int
    succeeded: int
    failed: int


class ProcessorAlreadyRunningError(RuntimeError):
    """Raised when another processing instance currently holds the lock."""


# Type alias for the mail processing function signature. The processor_baton can be used to pass
# contextual information (such as an IMAP config) without a global variable.
MailProcessor = Callable[[MailRecord, Any], None]


def default_db_path() -> Path:
    return Path.home() / ".local" / "state" / "omgmail" / "queue.sqlite3"


def build_config(args: argparse.Namespace) -> QueueConfig:
    db_path = Path(args.db_path).expanduser()
    emergency_dump_path = Path(args.emergency_dump).expanduser()
    lock_file_path = Path(args.lock_file).expanduser()
    return QueueConfig(
        db_path=db_path,
        emergency_dump_path=emergency_dump_path,
        lock_file_path=lock_file_path,
        busy_timeout_ms=args.busy_timeout_ms,
    )


def _open_connection(config: QueueConfig) -> sqlite3.Connection:
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.db_path, timeout=config.busy_timeout_ms / 1000)
    conn.execute(f"PRAGMA busy_timeout={config.busy_timeout_ms};")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            raw_content BLOB NOT NULL
        );

        CREATE TABLE IF NOT EXISTS failed_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_id INTEGER,
            failed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            error TEXT NOT NULL,
            raw_content BLOB NOT NULL
        );

        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)
    conn.commit()


def _append_to_emergency_dump(dump_path: Path, raw_message: bytes, reason: str) -> None:
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    with dump_path.open("ab") as dump_file:
        dump_file.write(f"\n--- OMGMAIL_EMERGENCY_DUMP {stamp} ---\n".encode())
        dump_file.write(f"reason={reason}\n".encode())
        dump_file.write(raw_message)
        if not raw_message.endswith(b"\n"):
            dump_file.write(b"\n")


def stash_new_mail(config: QueueConfig, raw_message: bytes | None = None) -> int:
    payload = raw_message if raw_message is not None else sys.stdin.buffer.read()
    if not payload:
        print("No message received on stdin", file=sys.stderr)
        return EX_TEMPFAIL

    try:
        with _open_connection(config) as conn:
            conn.execute("INSERT INTO queue (raw_content) VALUES (?)", (sqlite3.Binary(payload),))
            conn.commit()
    except (OSError, sqlite3.Error) as exc:
        _append_to_emergency_dump(config.emergency_dump_path, payload, str(exc))
        print(
            f"Database write failed ({exc}); wrote message to emergency dump at "
            f"{config.emergency_dump_path}",
            file=sys.stderr,
        )
        # Return success so procmail doesn't loop or bounce once fallback storage succeeds.
        return 0

    return 0


@contextmanager
def _singleton_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = lock_path.open("a", encoding="utf-8")
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProcessorAlreadyRunningError("processor lock is already held") from exc
        yield
    finally:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()


def _fetch_queue(conn: sqlite3.Connection, clear_queue: bool = False) -> list[MailRecord]:
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("SELECT id, received_at, raw_content FROM queue ORDER BY id").fetchall()
        if clear_queue:
            conn.execute("DELETE FROM queue")
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise

    return [MailRecord(id=row[0], received_at=row[1], raw_content=row[2]) for row in rows]


def _record_failed_job(conn: sqlite3.Connection, mail: MailRecord, error: Exception) -> None:
    conn.execute(
        """
        INSERT INTO failed_jobs (queue_id, error, raw_content)
        VALUES (?, ?, ?)
        """,
        (mail.id, str(error), sqlite3.Binary(mail.raw_content)),
    )
    conn.commit()


def iterate_queue(
    config: QueueConfig,
    remove_after_processing: bool = False,
    processor: MailProcessor | None = None,
    processor_baton: Any = None,
) -> ProcessResult:
    with _singleton_lock(config.lock_file_path):
        with _open_connection(config) as conn:
            pending = _fetch_queue(conn, clear_queue=remove_after_processing)

            succeeded = 0
            failed = 0
            for mail in pending:
                try:
                    if processor is not None:
                        processor(mail, processor_baton)
                    succeeded += 1
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    _record_failed_job(conn, mail, exc)

    return ProcessResult(total=succeeded + failed, succeeded=succeeded, failed=failed)


def count_queue_rows(config: QueueConfig) -> int:
    with _open_connection(config) as conn:
        row = conn.execute("SELECT COUNT(*) FROM queue").fetchone()
        assert row is not None
        return int(row[0])


def get_config_value(queue_config: QueueConfig, key: str) -> str | None:
    """Retrieve a config value from the database by key."""
    with _open_connection(queue_config) as conn:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None


def set_config_value(queue_config: QueueConfig, key: str, value: str) -> None:
    """Store or update a config value in the database."""
    with _open_connection(queue_config) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()


def delete_config_value(queue_config: QueueConfig, key: str) -> None:
    """Delete a config value from the database."""
    with _open_connection(queue_config) as conn:
        conn.execute("DELETE FROM config WHERE key = ?", (key,))
        conn.commit()


def list_config_values(queue_config: QueueConfig) -> dict[str, str]:
    """Retrieve all config values as a dictionary."""
    with _open_connection(queue_config) as conn:
        rows = conn.execute("SELECT key, value FROM config").fetchall()
        return {row[0]: row[1] for row in rows}


def get_imap_config_from_db(queue_config: QueueConfig) -> dict[str, Any]:
    """Retrieve all IMAP config settings from the database."""
    with _open_connection(queue_config) as conn:
        rows = conn.execute(
            "SELECT key, value FROM config WHERE key LIKE 'imap.%'",
        ).fetchall()
        stored = {row[0]: row[1] for row in rows}

    return {
        "host": stored["imap.host"],
        "port": int(stored.get("imap.port", 993)),
        "user": stored["imap.user"],
        "password": stored["imap.password"],
        "mailbox": stored.get("imap.mailbox", "ImportedInbox"),
        "mailbox_header": stored.get("imap.mailbox-header"),
    }
