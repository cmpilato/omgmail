import email
import email.utils
import imaplib
import sys
import time
from dataclasses import dataclass
from email.message import Message
from mailbox import mboxMessage

# ---- Configuration ----


@dataclass
class OMGMailIMAPConfig:
    imap_host: str | None = None
    imap_port: int = 993
    imap_user: str | None = None
    imap_password: str | None = None
    imap_mailbox: str | None = None

    def configured_imap(self) -> imaplib.IMAP4_SSL:
        if not all([self.imap_host, self.imap_user, self.imap_password, self.imap_mailbox]):
            raise ValueError("IMAP configuration is incomplete")

        assert self.imap_host is not None
        assert self.imap_user is not None
        assert self.imap_password is not None

        imap = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
        imap.login(self.imap_user, self.imap_password)
        return imap


def imap_date_from_message(msg: Message) -> str | None:
    """Return a quoted IMAP date-time string, or None if parsing fails."""
    date_hdr = msg.get("Date")
    if not date_hdr:
        return None

    try:
        parsed = email.utils.parsedate_tz(date_hdr)
        if not parsed:
            return None

        timestamp = email.utils.mktime_tz(parsed)
        date_str = time.strftime("%d-%b-%Y %H:%M:%S +0000", time.gmtime(timestamp))
        return f'"{date_str}"'
    except Exception:
        return None


def update_message(imap: imaplib.IMAP4_SSL, key: str, message: mboxMessage, mailbox: str) -> None:
    try:
        raw_message = message.as_bytes()
        imap_date = imap_date_from_message(message)
        imap.append(mailbox, None, imap_date, raw_message)
        print(f"Uploaded message {key}")
    except Exception as exc:
        print(f"Failed to upload message {key}: {exc}", file=sys.stderr)


def upload_messages(config: OMGMailIMAPConfig) -> None:
    imap = config.configured_imap()
    key: str
    message: mboxMessage
    try:
        for key, message in []:
            update_message(imap, key, message, config.imap_mailbox or "ImportedInbox")
    finally:
        imap.logout()


def ingest_message() -> int:
    raw_message = sys.stdin.buffer.read()
    if not raw_message:
        print("No message received on stdin", file=sys.stderr)
        return 75  # EX_TEMPFAIL, tells the MTA to retry

    return 0


def main() -> int:
    raise NotImplementedError(
        "This is a placeholder for the main function. The actual implementation is pending."
    )
