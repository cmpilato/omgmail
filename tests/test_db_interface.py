import sqlite3
import time
from pathlib import Path

from omgmail.db_interface import (
    QueueConfig,
    count_queue_rows,
    has_pending_mail,
    iterate_queue,
    stash_new_mail,
)


def _queue_config(tmp_path: Path) -> QueueConfig:
    return QueueConfig(
        db_path=tmp_path / "queue.sqlite3",
        emergency_dump_path=tmp_path / "emergency_dump.txt",
        lock_file_path=tmp_path / "queue.lock",
    )


def _sample_message(subject: str) -> bytes:
    return (
        f"From: sender@example.com\n"
        f"Date: Thu, 16 Apr 2026 12:34:56 +0000\n"
        f"Subject: {subject}\n"
        "\n"
        "Body\n"
    ).encode("utf-8")


def test_process_deletes_successes_and_keeps_failures_with_error(tmp_path: Path) -> None:
    config = _queue_config(tmp_path)
    assert stash_new_mail(config, _sample_message("ok")) == 0
    assert stash_new_mail(config, _sample_message("fail")) == 0

    def processor(mail, _baton) -> None:  # type: ignore[no-untyped-def]
        if b"Subject: fail" in mail.raw_content:
            raise RuntimeError("processing error")

    result = iterate_queue(config, readonly=False, processor=processor)
    assert result.total == 2
    assert result.succeeded == 1
    assert result.failed == 1
    assert count_queue_rows(config) == 1

    with sqlite3.connect(config.db_path) as conn:
        row = conn.execute(
            "SELECT processing_mark, processing_error FROM queue"
        ).fetchone()

    assert row is not None
    assert row[0]
    assert row[1] == "processing error"

    observed_errors: list[str | None] = []

    def observe(mail, _baton) -> None:  # type: ignore[no-untyped-def]
        observed_errors.append(mail.processing_error)

    iterate_queue(config, readonly=True, processor=observe)
    assert observed_errors == ["processing error"]


def test_has_pending_mail_tracks_queue_presence(tmp_path: Path) -> None:
    config = _queue_config(tmp_path)

    assert has_pending_mail(config) is False

    assert stash_new_mail(config, _sample_message("pending")) == 0
    assert has_pending_mail(config) is True

    iterate_queue(config, readonly=False)
    assert has_pending_mail(config) is False


def test_process_updates_mark_and_error_on_retry(tmp_path: Path) -> None:
    config = _queue_config(tmp_path)
    assert stash_new_mail(config, _sample_message("fail")) == 0

    def fail_first(mail, _baton) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("first error")

    first_result = iterate_queue(config, readonly=False, processor=fail_first)
    assert first_result.total == 1
    assert first_result.succeeded == 0
    assert first_result.failed == 1

    with sqlite3.connect(config.db_path) as conn:
        first_row = conn.execute(
            "SELECT processing_mark, processing_error FROM queue"
        ).fetchone()

    assert first_row is not None
    first_mark = first_row[0]
    assert first_mark
    assert first_row[1] == "first error"

    time.sleep(1.1)

    def fail_second(mail, _baton) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("second error")

    second_result = iterate_queue(config, readonly=False, processor=fail_second)
    assert second_result.total == 1
    assert second_result.succeeded == 0
    assert second_result.failed == 1

    with sqlite3.connect(config.db_path) as conn:
        second_row = conn.execute(
            "SELECT processing_mark, processing_error FROM queue"
        ).fetchone()

    assert second_row is not None
    assert second_row[0]
    assert second_row[0] != first_mark
    assert second_row[1] == "second error"
