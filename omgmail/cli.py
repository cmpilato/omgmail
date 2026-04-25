import argparse
import email
import logging
import shutil
import sys
from collections.abc import Sequence
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from . import __version__
from .db_interface import (
    MailRecord,
    ProcessorAlreadyRunningError,
    QueueConfig,
    build_config,
    default_db_path,
    delete_config_value,
    get_config_value,
    get_imap_config_from_db,
    has_pending_mail,
    iterate_queue,
    list_config_values,
    set_config_value,
    stash_new_mail,
)
from .imap_interface import OMGMailIMAPConfig, upload_mail_record

LOGGER = logging.getLogger(__name__)
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _build_arg_parser() -> argparse.ArgumentParser:
    default_db = default_db_path()

    parser = argparse.ArgumentParser(
        description="Store incoming mail in a SQLite queue and process it later."
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"OMGmail {__version__}",
    )
    parser.add_argument(
        "--db-path",
        default=str(default_db),
        help="SQLite queue DB path (default: %(default)s)",
    )
    parser.add_argument(
        "--emergency-dump",
        default=str(default_db.with_name("emergency_dump.txt")),
        help="Emergency fallback file when DB writes fail (default: %(default)s)",
    )
    parser.add_argument(
        "--lock-file",
        default=str(default_db.with_suffix(".lock")),
        help="Processor lock file path (default: %(default)s)",
    )
    parser.add_argument(
        "--busy-timeout-ms",
        type=int,
        default=30_000,
        help="SQLite busy timeout in milliseconds (default: %(default)s)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("ingest", help="Read a message from stdin and queue it")
    subparsers.add_parser("process", help="Process a batch of queued messages")
    subparsers.add_parser("queue", help="Print a summary table of queued messages")

    # Config subcommands
    config_parser = subparsers.add_parser("config", help="Manage configuration")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)

    config_subparsers.add_parser("list", help="List all configuration values")

    get_parser = config_subparsers.add_parser("get", help="Get a configuration value")
    get_parser.add_argument("key", help="Configuration key (e.g., 'imap.host')")

    set_parser = config_subparsers.add_parser("set", help="Set a configuration value")
    set_parser.add_argument("key", help="Configuration key (e.g., 'imap.host')")
    set_parser.add_argument("value", help="Configuration value")

    delete_parser = config_subparsers.add_parser("delete", help="Delete a configuration value")
    delete_parser.add_argument("key", help="Configuration key (e.g., 'imap.host')")

    return parser


def _decode_mail_header(value: str | None) -> str:
    if not value:
        return ""

    decoded_parts: list[str] = []
    for part, encoding in decode_header(value):
        if isinstance(part, bytes):
            decoded_parts.append(part.decode(encoding or "utf-8", errors="replace"))
        else:
            decoded_parts.append(part)
    return "".join(decoded_parts).replace("\r", " ").replace("\n", " ").strip()


def _truncate_to_width(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[: width - 1] + "…"


def _sender_from_header(from_header: str | None) -> str:
    decoded = _decode_mail_header(from_header)
    name, address = parseaddr(decoded)
    if name and address:
        return f"{name} <{address}>"
    if address:
        return address
    return decoded or "(unknown sender)"


def _sent_date_from_header(date_header: str | None, fallback: str) -> str:
    decoded = _decode_mail_header(date_header)
    if not decoded:
        return fallback
    try:
        parsed = parsedate_to_datetime(decoded)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _mail_summary_from_record(mail: MailRecord) -> tuple[str, str, str]:
    message = email.message_from_bytes(mail.raw_content)
    sender = _sender_from_header(message.get("From"))
    sent_date = _sent_date_from_header(message.get("Date"), mail.received_at)
    subject = _decode_mail_header(message.get("Subject")) or "(no subject)"
    return sender, sent_date, subject


def _do_ingest(queue_config: QueueConfig) -> int:
    return stash_new_mail(queue_config)


def _configure_logging(root_logger: logging.Logger | None = None) -> None:
    logger = root_logger or logging.getLogger()
    if not logger.handlers:
        logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)


def _do_process(queue_config: QueueConfig) -> int:
    _configure_logging()

    if not has_pending_mail(queue_config):
        LOGGER.info("Queue is empty; nothing to process.")
        return 0

    try:
        stored = get_imap_config_from_db(queue_config)
    except KeyError as e:
        LOGGER.error(f"Missing required IMAP config value: {e}")
        return 1
    except Exception as e:
        LOGGER.error(f"Failed to read IMAP config from DB: {e}")
        return 1

    imap_config = None
    if stored["host"]:
        imap_config = OMGMailIMAPConfig(
            imap_host=stored["host"],
            imap_port=int(stored["port"]) if stored["port"] else 993,
            imap_user=stored["user"],
            imap_password=stored["password"],
            imap_mailbox=stored["mailbox"],
            imap_mailbox_header=stored["mailbox_header"],
        )

    if not imap_config:
        LOGGER.error("IMAP configuration is required")
        return 1

    try:

        def processor(mail: MailRecord, imap_session: Any) -> None:
            sender, sent_date, subject = _mail_summary_from_record(mail)
            LOGGER.info(
                f"Processing mail id={mail.id} date={sent_date} from={sender} subject={subject}"
            )
            upload_mail_record(mail, imap_config, imap=imap_session)

        with imap_config.with_configured_imap() as imap_session:
            result = iterate_queue(
                queue_config,
                readonly=False,
                processor=processor,
                processor_baton=imap_session,
            )
    except ProcessorAlreadyRunningError:
        LOGGER.warning("Another processor instance is already running; exiting.")
        return 0

    LOGGER.info(
        f"Processed batch: total={result.total}, succeeded={result.succeeded}, "
        f"failed={result.failed}"
    )
    return 0


def _do_queue(queue_config: QueueConfig) -> int:
    terminal_width = shutil.get_terminal_size(fallback=(100, 20)).columns
    id_width = 4
    from_width = 22
    date_width = 19
    mark_width = 20
    error_width = 24

    header = (
        f"{'ID':>{id_width}}  "
        f"{'FROM':<{from_width}}  "
        f"{'QUEUED':<{date_width}}  "
        f"{'LAST TRY':<{mark_width}}  "
        f"{'ERROR':<{error_width}}  "
        "SUBJECT"
    )
    print(_truncate_to_width(header, terminal_width))

    def processor(mail: MailRecord, _: Any) -> None:
        sender, sent_date, subject = _mail_summary_from_record(mail)
        processing_mark = (mail.processing_mark or "").replace("T", " ")[:mark_width]
        processing_error = mail.processing_error or ""
        line = (
            f"{mail.id:>{id_width}}  "
            f"{_truncate_to_width(sender, from_width):<{from_width}}  "
            f"{sent_date:<{date_width}}  "
            f"{_truncate_to_width(processing_mark, mark_width):<{mark_width}}  "
            f"{_truncate_to_width(processing_error, error_width):<{error_width}}  "
            f"{subject}"
        )
        print(_truncate_to_width(line, terminal_width))

    result = iterate_queue(queue_config, readonly=True, processor=processor)
    print(f"\nTotal messages in queue: {result.total}")
    return 0


def _do_config(queue_config: QueueConfig, args: argparse.Namespace) -> int:
    if args.config_command == "list":
        values = list_config_values(queue_config)
        if not values:
            print("No configuration values set.")
            return 0
        for key, value in sorted(values.items()):
            print(f"{key}={value}")
        return 0

    if args.config_command == "get":
        value = get_config_value(queue_config, args.key) or ""
        if not value:
            print(f"No value set for '{args.key}'", file=sys.stderr)
            return 1
        print(value)
        return 0

    if args.config_command == "set":
        set_config_value(queue_config, args.key, args.value)
        print(f"Set {args.key}={args.value}")
        return 0

    if args.config_command == "delete":
        delete_config_value(queue_config, args.key)
        print(f"Deleted {args.key}")
        return 0

    print(f"Unknown config command: {args.config_command}", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    queue_config = build_config(args)

    if args.command == "ingest":
        return _do_ingest(queue_config)

    if args.command == "process":
        return _do_process(queue_config)

    if args.command == "queue":
        return _do_queue(queue_config)

    if args.command == "config":
        return _do_config(queue_config, args)

    parser.error(f"Unknown command: {args.command}")
    return 2
