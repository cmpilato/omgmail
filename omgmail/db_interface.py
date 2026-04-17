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
    processing_mark: str | None = None
    processing_error: str | None = None


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
            raw_content BLOB NOT NULL,
            processing_mark TEXT,
            processing_error TEXT
        );

        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)

    # Migrate existing queue tables created before processing columns existed.
    queue_columns = {row[1] for row in conn.execute("PRAGMA table_info(queue)").fetchall()}
    if "processing_mark" not in queue_columns:
        conn.execute("ALTER TABLE queue ADD COLUMN processing_mark TEXT")
    if "processing_error" not in queue_columns:
        conn.execute("ALTER TABLE queue ADD COLUMN processing_error TEXT")
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


def _fetch_queue(
    conn: sqlite3.Connection,
    mark_for_processing: bool = False,
) -> list[MailRecord]:
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("""
            SELECT id, received_at, raw_content, processing_mark, processing_error
            FROM queue
            ORDER BY id
            """).fetchall()
        if mark_for_processing and rows:
            processing_mark = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
            row_ids = [int(row[0]) for row in rows]
            placeholders = ",".join("?" for _ in row_ids)
            conn.execute(
                f"""
                UPDATE queue
                SET processing_mark = ?, processing_error = NULL
                WHERE id IN ({placeholders})
                """,
                (processing_mark, *row_ids),
            )
            rows = [(row[0], row[1], row[2], processing_mark, None) for row in rows]
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise

    return [
        MailRecord(
            id=row[0],
            received_at=row[1],
            raw_content=row[2],
            processing_mark=row[3],
            processing_error=row[4],
        )
        for row in rows
    ]


def _delete_queue_mail(conn: sqlite3.Connection, mail: MailRecord) -> None:
    conn.execute("DELETE FROM queue WHERE id = ?", (mail.id,))
    conn.commit()


def _mark_queue_mail_failed(conn: sqlite3.Connection, mail: MailRecord, error: Exception) -> None:
    conn.execute(
        "UPDATE queue SET processing_error = ? WHERE id = ?",
        (str(error), mail.id),
    )
    conn.commit()


def iterate_queue(
    config: QueueConfig,
    readonly: bool = False,
    processor: MailProcessor | None = None,
    processor_baton: Any = None,
) -> ProcessResult:
    with _singleton_lock(config.lock_file_path):
        with _open_connection(config) as conn:
            pending = _fetch_queue(conn, mark_for_processing=not readonly)

            succeeded = 0
            failed = 0
            for mail in pending:
                try:
                    if processor is not None:
                        processor(mail, processor_baton)
                    succeeded += 1
                    if not readonly:
                        _delete_queue_mail(conn, mail)
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    if not readonly:
                        _mark_queue_mail_failed(conn, mail, exc)

    return ProcessResult(total=succeeded + failed, succeeded=succeeded, failed=failed)


def count_queue_rows(config: QueueConfig) -> int:
    with _open_connection(config) as conn:
        row = conn.execute("SELECT COUNT(*) FROM queue").fetchone()
        assert row is not None
        return int(row[0])


def has_pending_mail(config: QueueConfig) -> bool:
    with _open_connection(config) as conn:
        row = conn.execute("SELECT 1 FROM queue LIMIT 1").fetchone()
        return row is not None


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
