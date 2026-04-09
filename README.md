# omgmail - Gmail IMAP gateway

This is a very underdeveloped work in progress, but the goal is to solve the problem of Gmail announcing that they are discontinuing support for the "POP from another account" feature.  (Which, by the way, is a terribly cruel thing to do to users who both need a vanity email address from a domain they don't control and love Gmail's feature set.  Just saying...)

OMGmail intends to become a tool that can be used in two ways:

- As a fetchmail pipe, ingesting emails on-demand via stdin into a storage layer.
- As a scheduled (cron-driven, e.g.) processor of those ingested mails, injecting them via IMAP into Gmail and then removing them from the storage layer.

## Package name

- Distribution/package name: `OMGmail`
- Import/module name: `omgmail`

## Install

From the repository root:

```bash
python -m pip install -e .
```

## Run

Use the installed console script:

```bash
omgmail [...]
```
