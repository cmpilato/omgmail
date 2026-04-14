from email.message import EmailMessage

from omgmail.imap_interface import OMGMailIMAPConfig, mailbox_from_message


def test_mailbox_from_message_uses_configured_default() -> None:
    msg = EmailMessage()
    msg["Subject"] = "default mailbox"

    config = OMGMailIMAPConfig(imap_mailbox="INBOX")

    assert mailbox_from_message(msg, config) == "INBOX"


def test_mailbox_from_message_uses_header_override() -> None:
    msg = EmailMessage()
    msg["Subject"] = "override mailbox"
    msg["X-OMGmail-IMAP-Folder"] = "Archive/2026"

    config = OMGMailIMAPConfig(
        imap_mailbox="INBOX",
        imap_mailbox_header="X-OMGmail-IMAP-Folder",
    )

    assert mailbox_from_message(msg, config) == "Archive/2026"


def test_mailbox_from_message_ignores_blank_header_override() -> None:
    msg = EmailMessage()
    msg["Subject"] = "blank override"
    msg["X-OMGmail-IMAP-Folder"] = "   "

    config = OMGMailIMAPConfig(
        imap_mailbox="INBOX",
        imap_mailbox_header="X-OMGmail-IMAP-Folder",
    )

    assert mailbox_from_message(msg, config) == "INBOX"