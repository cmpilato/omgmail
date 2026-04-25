from email.message import EmailMessage

from omgmail.db_interface import MailRecord
from omgmail.imap_interface import OMGMailIMAPConfig, mailbox_from_message, upload_mail_record


def test_get_imap_mailbox_falls_back_to_inbox_for_falsey_values() -> None:
    assert OMGMailIMAPConfig(imap_mailbox=None).get_imap_mailbox() == "INBOX"
    assert OMGMailIMAPConfig(imap_mailbox="").get_imap_mailbox() == "INBOX"


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


class _FakeIMAP:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.login_calls: list[tuple[str, str]] = []
        self.logout_calls = 0
        self.append_calls: list[tuple[str, str, str, bytes]] = []

    def login(self, user: str, password: str) -> None:
        self.login_calls.append((user, password))

    def logout(self) -> None:
        self.logout_calls += 1

    def append(self, mailbox: str, flags: str, imap_date: str, raw_content: bytes) -> None:
        self.append_calls.append((mailbox, flags, imap_date, raw_content))


def test_with_configured_imap_context_logs_in_and_out(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    created: list[_FakeIMAP] = []

    def _factory(host: str, port: int) -> _FakeIMAP:
        imap = _FakeIMAP(host, port)
        created.append(imap)
        return imap

    monkeypatch.setattr("omgmail.imap_interface.imaplib.IMAP4_SSL", _factory)

    config = OMGMailIMAPConfig(
        imap_host="imap.example.com",
        imap_port=993,
        imap_user="user@example.com",
        imap_password="secret",
        imap_mailbox="INBOX",
    )

    with config.with_configured_imap() as imap:
        assert imap.host == "imap.example.com"

    assert len(created) == 1
    assert created[0].login_calls == [("user@example.com", "secret")]
    assert created[0].logout_calls == 1


def test_upload_mail_record_reuses_existing_imap() -> None:
    config = OMGMailIMAPConfig(imap_mailbox="INBOX")
    fake_imap = _FakeIMAP("imap.example.com", 993)
    raw = (
        "From: sender@example.com\n"
        "Date: Thu, 16 Apr 2026 12:34:56 +0000\n"
        "Subject: test upload\n"
        "\n"
        "Body\n"
    ).encode("utf-8")
    mail = MailRecord(id=1, received_at="2026-04-16 12:34:56", raw_content=raw)

    upload_mail_record(mail, config, imap=fake_imap)

    assert len(fake_imap.append_calls) == 1
    assert fake_imap.append_calls[0][0] == "INBOX"
    assert fake_imap.logout_calls == 0