import logging
import os
from contextlib import contextmanager
from pathlib import Path

from omgmail.cli import LOG_DATE_FORMAT, LOG_FORMAT, _configure_logging, _do_process, _do_queue
from omgmail.db_interface import QueueConfig, set_config_value, stash_new_mail


def _queue_config(tmp_path: Path) -> QueueConfig:
    return QueueConfig(
        db_path=tmp_path / "queue.sqlite3",
        emergency_dump_path=tmp_path / "emergency_dump.txt",
        lock_file_path=tmp_path / "queue.lock",
    )


def test_process_returns_without_imap_config_when_queue_is_empty(tmp_path: Path) -> None:
    config = _queue_config(tmp_path)

    assert _do_process(config) == 0


def test_configure_logging_uses_datestamped_format(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    recorded: dict[str, object] = {}

    class _RootLogger:
        handlers: list[object] = []

    def fake_basic_config(**kwargs: object) -> None:
        recorded.update(kwargs)

    monkeypatch.setattr("omgmail.cli.logging.basicConfig", fake_basic_config)

    _configure_logging(root_logger=_RootLogger())

    assert recorded == {
        "level": logging.INFO,
        "format": LOG_FORMAT,
        "datefmt": LOG_DATE_FORMAT,
    }


def test_process_logs_message_context_for_upload_failures(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:  # type: ignore[no-untyped-def]
    config = _queue_config(tmp_path)
    raw_message = (
        b"From: Sender Example <sender@example.com>\n"
        b"Date: Thu, 16 Apr 2026 12:34:56 +0000\n"
        b"Subject: Upload Failure \xe2\x80\x94 urgent\n"
        b"\n"
        b"Body\n"
    )
    assert stash_new_mail(config, raw_message) == 0

    set_config_value(config, "imap.host", "imap.example.com")
    set_config_value(config, "imap.user", "user")
    set_config_value(config, "imap.password", "secret")

    @contextmanager
    def fake_with_configured_imap(self):  # type: ignore[no-untyped-def]
        yield object()

    def fail_upload(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated upload failure")

    monkeypatch.setattr(
        "omgmail.cli.OMGMailIMAPConfig.with_configured_imap",
        fake_with_configured_imap,
    )
    monkeypatch.setattr("omgmail.cli.upload_mail_record", fail_upload)

    caplog.set_level(logging.INFO)

    assert _do_process(config) == 0

    assert "Processed batch: total=1, succeeded=0, failed=1" in caplog.text
    assert "Processing failed for mail id=1" in caplog.text
    assert "Sender Example <sender@example.com>" in caplog.text
    assert "Upload Failure" in caplog.text
    assert "simulated upload failure" in caplog.text


def test_queue_displays_mail_with_unknown_8bit_headers(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:  # type: ignore[no-untyped-def]
    config = _queue_config(tmp_path)
    raw_message = (
        b"From: Sender Example <sender@example.com>\n"
        b"Date: Thu, 16 Apr 2026 12:34:56 +0000\n"
        b"Subject: Queue Summary \xe2\x80\x94 visible\n"
        b"\n"
        b"Body\n"
    )
    assert stash_new_mail(config, raw_message) == 0
    monkeypatch.setattr("omgmail.cli.shutil.get_terminal_size", lambda fallback: os.terminal_size((200, 20)))

    assert _do_queue(config) == 0

    output = capsys.readouterr().out
    assert "Sender Example" in output
    assert "Queue Summary" in output
    assert "Total messages in queue: 1" in output