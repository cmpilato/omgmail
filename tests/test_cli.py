import logging
from pathlib import Path

from omgmail.cli import LOG_DATE_FORMAT, LOG_FORMAT, _configure_logging, _do_process
from omgmail.db_interface import QueueConfig


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