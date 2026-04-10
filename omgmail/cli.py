import argparse
import sys
from collections.abc import Sequence

from .db_interface import (
    ProcessorAlreadyRunningError,
    count_queue_rows,
    build_config,
    default_db_path,
    delete_config_value,
    get_config_value,
    get_imap_config_from_db,
    list_config_values,
    process_current_mails,
    set_config_value,
    stash_new_mail,
)
from .imap_interface import OMGMailIMAPConfig


def _build_arg_parser() -> argparse.ArgumentParser:
    default_db = default_db_path()

    parser = argparse.ArgumentParser(
        description="Store incoming mail in a SQLite queue and process it later."
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
    subparsers.add_parser("process", help="Atomically fetch+clear queue and process messages")
    subparsers.add_parser("stats", help="Print queue counts")

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


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    queue_config = build_config(args)

    if args.command == "ingest":
        return stash_new_mail(queue_config)

    if args.command == "process":
        stored = get_imap_config_from_db(queue_config)
        imap_config = None
        if stored["host"]:
            imap_config = OMGMailIMAPConfig(
                imap_host=stored["host"],
                imap_port=int(stored["port"]) if stored["port"] else 993,
                imap_user=stored["user"],
                imap_password=stored["password"],
                imap_mailbox=stored["mailbox"],
            )
        try:
            result = process_current_mails(queue_config, imap_config=imap_config)
        except ProcessorAlreadyRunningError:
            print("Another processor instance is already running; exiting.", file=sys.stderr)
            return 0

        print(
            f"Processed batch: total={result.total}, "
            f"succeeded={result.succeeded}, failed={result.failed}"
        )
        return 0

    if args.command == "stats":
        queue_count = count_queue_rows(queue_config)
        print(f"queue={queue_count}")
        return 0

    if args.command == "config":
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

    parser.error(f"Unknown command: {args.command}")
    return 2
