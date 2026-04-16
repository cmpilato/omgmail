import email
import email.utils
import imaplib
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import Message

from .db_interface import MailRecord

LOGGER = logging.getLogger(__name__)


@dataclass
class OMGMailIMAPConfig:
    imap_host: str | None = None
    imap_port: int = 993
    imap_user: str | None = None
    imap_password: str | None = None
    imap_mailbox: str | None = None
    imap_mailbox_header: str | None = None

    @contextmanager
    def with_configured_imap(self) -> Iterator[imaplib.IMAP4_SSL]:
        if not all([self.imap_host, self.imap_user, self.imap_password, self.imap_mailbox]):
            raise ValueError("IMAP configuration is incomplete")

        assert self.imap_host is not None
        assert self.imap_user is not None
        assert self.imap_password is not None

        LOGGER.info(
            f"Logging into IMAP server host={self.imap_host} port={self.imap_port} "
            f"user={self.imap_user}"
        )
        imap = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
        imap.login(self.imap_user, self.imap_password)
        try:
            yield imap
        finally:
            LOGGER.info("Logging out from IMAP server")
            imap.logout()


def imap_date_from_message(msg: Message) -> str:
    """Return a quoted IMAP date-time string."""
    try:
        date_hdr = msg["Date"]
        parsed = email.utils.parsedate_tz(date_hdr)
        if not parsed:
            raise RuntimeError("Failed to parse date header")
        timestamp = time.gmtime(email.utils.mktime_tz(parsed))
    except Exception:
        timestamp = time.gmtime()
    date_str = time.strftime("%d-%b-%Y %H:%M:%S +0000", timestamp)
    return f'"{date_str}"'


def mailbox_from_message(msg: Message, config: OMGMailIMAPConfig) -> str:
    header_name = config.imap_mailbox_header
    if header_name:
        header_value = msg.get(header_name)
        if header_value is not None:
            candidate = str(header_value).strip()
            if candidate:
                return candidate

    return config.imap_mailbox or "ImportedInbox"


def upload_mail_record(
    mail: MailRecord,
    config: OMGMailIMAPConfig,
    imap: imaplib.IMAP4_SSL,
) -> None:
    """
    Parse a MailRecord's raw bytes, extract the date, and upload to IMAP.
    Raises exceptions on parse or upload failures for DB error tracking.
    """

    msg = email.message_from_bytes(mail.raw_content)
    imap_date = imap_date_from_message(msg)
    mailbox = mailbox_from_message(msg, config)
    try:
        LOGGER.info(f"Appending message id={mail.id} to mailbox='{mailbox}'...")
        imap.append(mailbox, "", imap_date, mail.raw_content)
        LOGGER.info(f"Uploaded message id={mail.id}")
    except Exception as e:
        LOGGER.error(f"Failed to upload message id={mail.id}: {e}")
        raise
