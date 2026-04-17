from pathlib import Path

from omgmail.cli import _do_process
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