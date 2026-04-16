# omgmail - SQLite mail queue bridge

`omgmail` is a lightweight bridge between a `procmail` injection path and a scheduled processor. Incoming mail is queued in SQLite and later consumed in batches by a separate process.

Current implementation focuses on a robust queue foundation:

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
omgmail config set imap.mailbox "INBOX"
omgmail config set imap.mailbox-header "X-OMGmail-IMAP-Folder"
```

If `imap.mailbox-header` is configured, `process` will inspect each message for that
header and, when present with a non-empty value, use that value as the destination
IMAP folder for that message. Messages without the header continue to use the global
`imap.mailbox` value.

### Procmail Recipe

```procmail
:0
| /usr/bin/omgmail ingest --db-path /var/spool/omgmail/queue.sqlite3
```

### Cron Processor

```cron
*/2 * * * * /usr/bin/omgmail process --db-path /var/spool/omgmail/queue.sqlite3 >>/var/log/omgmail.log 2>&1
```
