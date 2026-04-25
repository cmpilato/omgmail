# omgmail - SQLite mail queue bridge

`omgmail` is a lightweight bridge between an email MTA and a remote IMAP destination,
collecting emails injected in real time into it via a typical Unix mail pipe, then
depositing those mails into a remote IMAP folder system.

The current implementation focuses on a robust queue foundation:

- SQLite queue with `WAL` mode and configurable busy timeout.
- Injection command that reads raw mail from `stdin` and stores it quickly.
- Emergency flat-file fallback when SQLite is unavailable.
- Processor command with single-instance locking and atomic fetch-and-clear semantics.

## Package name

- Distribution/package name: `OMGmail`
- Import/module name: `omgmail`

## Install

From the repository root:

```bash
python -m pip install -e .
```

## Commands

Use the installed console script:

```bash
omgmail [global-options] <command>
```

Commands:

- `ingest`: reads one raw message from `stdin` and inserts into the queue.
- `process`: atomically marks all queued rows for a processing attempt, processes each row, deletes
    successful rows, and keeps failed rows with a stored processing error.
- `queue`: prints a queue summary table (ID, sender, sent date, last processing attempt, processing
    error, and subject) with one message per line.
- `config`: manage persistent configuration in the database.
  - `config set <key> <value>`: set a configuration value (e.g., `config set imap.host "imap.gmail.com"`).
  - `config get <key>`: get a configuration value (e.g., `config get imap.host`).
  - `config delete <key>`: delete a configuration value (e.g., `config delete imap.password`).
  - `config list`: list all stored configuration values.

Global options:

- `--db-path`: SQLite DB path (default: `~/.local/state/omgmail/queue.sqlite3`).
- `--emergency-dump`: fallback file path for DB failures.
- `--lock-file`: lock file path used to enforce single processor instance.
- `--busy-timeout-ms`: SQLite busy timeout in milliseconds (default: `30000`).

## Example Integration

### Setup

Store IMAP credentials in the database:

```bash
omgmail config set imap.host "imap.gmail.com"
omgmail config set imap.port "993"
omgmail config set imap.user "user@gmail.com"
omgmail config set imap.password "app-password"
```

By default, OMGmail will deposit mails that it processes into the "INBOX"
IMAP folder.  But you can change this with an additional configuration:

```bash
omgmail config set imap.mailbox "SomeOtherFolder"
```

And if you have the ability to pre-process incoming email and inject
headers conditionally (using, for example, `procmail` and `formail`),
you can configure OMGmail to recognize a custom header whose value
overrides the default destination IMAP folder _for that specific email_.

```bash
omgmail config set imap.mailbox-header "X-OMGmail-IMAP-Folder"
```

### Ingestion

With a simple `.forward` file, you can pipe incoming mail to OMGmail:

```
|/usr/bin/omgmail ingest --db-path /var/spool/omgmail/queue.sqlite3
```

For more complicated setups, you might have your `.forward` send mails
through `procmail`:

```
|/usr/bin/procmail
```

...and then have your `.procmailrc` file customize the destination
header before passing the email to OMGmail:

```
[...]

:0fw
* ^From:.*president@whitehouse.gov
| formail -I "X-OMGmail-Folder: Important"

:0
| /usr/bin/omgmail ingest --db-path /var/spool/omgmail/queue.sqlite3
```

### Processing

Because processing involves authentication with a remote IMAP server,
it's recommended that that cost _not_ be paid as part of the ingestion
pipeline.  But you can drive processing as a cron job, for example:

```cron
# Upload emails to the remote IMAP every five minutes.
*/5 * * * * /usr/bin/omgmail process --db-path /var/spool/omgmail/queue.sqlite3
```

## Cool, But ... Why?

In late 2025/early 2026, Google more-or-less-silently "announced" their intentions to
[shut down the "Check emails from other accounts" feature](https://support.google.com/mail/answer/16604719?hl=en),
much to the utter shock and horror of countless users who love both Gmail's brilliant
interface and spam protection _and_ the vanity email addresses they've been using for
decades.  C. Michael Pilato was just such a user, and decided to create OMGmail so
that he could keep using his primary self-managed-domain email address while still
using the Gmail interface to access it.

Now, many users can get around this situation by simply forwarding emails from their
personalized accounts to their Gmail accounts, Pilato's email was associated with a
long-running domain that has hosted mailing lists, open source software projects, etc.
Such domains are spam targets, and not everybody has Google-sized resources to fight
that battle.  To naively forward email from the private server to Google's mail servers
would eventually cause the former to be blacklisted as a spam relay.  (This is not mere
conjecture -- it had already happened in the past.)

Other users in similar situations might simply allow Google to be their domain's
mail handler outright.  But trying to achieve consensus on such a domain-wide
change among a bunch of privacy-minded old-school hackers ... not happening.

So, since Gmail would eventually no longer "pull" email from the private server,
Pilato reasoned that the next best thing would be for the private server to "push"
his email into Gmail via IMAP.  He loses the spam protection that Google offers
for mails it routes outright, but he gets to keep the Gmail interface.  (And his
mails get delivered according to _his_ schedule rather than the often-long-delayed
poll rate that Gmail used when pulling emails via POP.)

