# Backups

The data is the SQLite database on the Fly volume (`/data/db.sqlite3`). This
page covers how snapshots are taken, how the timer works, how to switch on the
optional private off-site copy, and how to prove a backup restores. Local
snapshots and the timer are always on; off-site uploads stay disabled until
configured.

## What runs

| Piece | What it does |
|---|---|
| `manage.py backup_database` | One verified snapshot now, then local pruning. Runs at every boot in `scripts/docker-entrypoint.sh`, before `migrate`. |
| `python -m core.backup_scheduler` | The timer. Started in the background by the entrypoint after `migrate`. |
| `manage.py backup_scheduler` | Same timer through Django's command runner (`--once`, `--force`). Handy by hand. |
| `manage.py backup_status` | Prints the persisted state; `--check` exits non-zero if the local backup is overdue (more than two intervals) or the off-site upload is failing; `--json` for monitors. Never prints credentials. |
| `manage.py restore_backup_drill` | Restores a snapshot into a **new** scratch database and checks it. |

Code: `core/backup.py` (snapshots, state, locks), `core/backup_remote.py`
(S3 upload), `core/backup_scheduler.py` (timer).

### Snapshots

Snapshots use SQLite's online backup API, so the copy is consistent under WAL
(committed rows that are not yet checkpointed are included, and a concurrent
writer cannot tear it). Each is written as `db-YYYYMMDD-HHMMSS.sqlite3` (UTC;
`-2`, `-3` if two land in the same second) into `backups/` next to the
database (`/data/backups/`), by way of a `.partial` file that is checked with
`PRAGMA quick_check` and only then renamed, so a crash or full disk never
leaves something that looks like a backup. The result is one self-contained
file (journal mode DELETE), so a download needs no `-wal`/`-shm`.

### The timer

- One process on the existing single machine. A lock file
  (`backups/.scheduler.lock`, a kernel `flock`, released automatically if the
  process dies) makes a second timer exit immediately, and a separate lock
  serialises every backup, so a manual `backup_database` can never overlap the
  timer.
- It wakes at least every `BACKUP_CHECK_SECONDS` (default and maximum 60) and
  compares the **wall clock** with `last_local_success_at` stored in
  `backups/backup-state.json`. It never counts elapsed sleep, so a machine
  Fly suspended for days takes one catch-up backup within a minute of waking,
  and a restart resumes from the stored timestamp. A last-success time in the
  future (clock set back) counts as overdue.
- After a failed backup it retries after `BACKUP_RETRY_SECONDS` (default 900),
  doubling each time up to the interval. A missing database file (first boot)
  is waited for, not counted as a failure. The boot backup records its success,
  so the timer does not immediately repeat it.
- The machine only runs while awake. Fly suspends it when idle, so with no
  traffic there is nothing new to protect; the first request after the
  interval wakes it and the next check (within 60 s) takes the backup.
- It exits on SIGTERM/SIGINT (a running backup or upload is torn down and
  leaves nothing half-written) and when its parent shell/gunicorn process is
  gone (`--parent-pid "$$"` in the entrypoint). A crash or bad configuration is
  logged; the web server keeps serving either way.
- It is a separate Python process that loads only Django's settings, not the
  apps: roughly 30–40 MB resident. Watch `fly status` and the metrics for memory
  pressure on the 256 MB machine.

## Configuration

All keys are environment variables, read in `config/settings.py` as raw
strings and validated when a backup runs: a typo can never stop the app from
booting. The local scheduling numbers (`BACKUP_INTERVAL_HOURS`,
`BACKUP_CHECK_SECONDS`, `BACKUP_RETRY_SECONDS`, `BACKUP_KEEP_LOCAL`) are
clamped when out of range and fall back to the default when unparsable; the
two on/off switches fall back to their default when unparsable. Each is logged
as a warning at scheduler start. The off-site values are never clamped or
defaulted: an invalid one, including a `BACKUP_UPLOAD_TIMEOUT_SECONDS` that is
not an integer from 10 to 3600, is rejected and disables uploads while local
backups go on.

| Key | Default | Meaning |
|---|---|---|
| `BACKUP_SCHEDULER_ENABLED` | `true` | Run the timer at all (`false` leaves only the boot backup). |
| `BACKUP_INTERVAL_HOURS` | `24` | Time between snapshots (0.25–168). |
| `BACKUP_CHECK_SECONDS` | `60` | How often the timer looks at the clock (5–60). |
| `BACKUP_RETRY_SECONDS` | `900` | First retry delay after a failure (60–86400). |
| `BACKUP_KEEP_LOCAL` | `7` | Local snapshots kept (1–365). Also the default for `backup_database --keep`. |
| `BACKUP_DIR` | `<db dir>/backups` | Where snapshots and `backup-state.json` live. |
| `BACKUP_REMOTE_ENABLED` | `false` | Master switch for off-site upload. Everything below is ignored while it is not true. |
| `BACKUP_S3_REGION` | – (required) | e.g. `eu-central-1`; `auto` for providers that ask for it. |
| `BACKUP_S3_BUCKET` | – (required) | Bucket name (S3 naming rules). |
| `BACKUP_S3_ACCESS_KEY_ID` | – (required) | |
| `BACKUP_S3_SECRET_ACCESS_KEY` | – (required) | |
| `BACKUP_S3_SESSION_TOKEN` | empty | Only for temporary credentials. |
| `BACKUP_S3_ENDPOINT` | AWS S3 for the region | Bare `https://host[:port]` for S3-compatible stores (R2, B2, MinIO, …). No path, query, fragment or credentials. |
| `BACKUP_S3_PREFIX` | empty | Folder inside the bucket, e.g. `flashcards/prod`. |
| `BACKUP_S3_ADDRESSING` | `virtual` on AWS, `path` for a custom endpoint | Override with `path` or `virtual`. |
| `BACKUP_UPLOAD_TIMEOUT_SECONDS` | `300` | Hard limit per upload (10–3600); connect timeout is a fixed 10 s. |

Local scheduling is on by default (24 h, keep 7). The off-site copy is **off**
until `BACKUP_REMOTE_ENABLED=true` *and* every required value validates. If it
is enabled but incomplete or invalid the scheduler still takes local backups,
logs an error naming the offending setting (never its value), and uploads
nothing; `backup_status` shows `remote: misconfigured`. The upload runs inside
the timer, so `BACKUP_SCHEDULER_ENABLED=false` also pauses it.

### Setting it up

Pick a private bucket first. Block all public access, leave ACLs disabled, and
create an access key that can only `s3:PutObject` under the prefix (it does not
need to read, list or delete). Then, from the repository root:

    fly secrets import --app <app> --stage \
      < /secure/path/backup-credentials.env
    # non-secret values may live in fly.toml [env] or be set the same way:
    fly secrets set --app <app> \
      BACKUP_REMOTE_ENABLED=true BACKUP_S3_REGION=eu-central-1 \
      BACKUP_S3_BUCKET=my-flashcards-backups BACKUP_S3_PREFIX=flashcards/prod

The import reads an existing private credentials file outside the repository,
with one `NAME=VALUE` per line for `BACKUP_S3_ACCESS_KEY_ID`,
`BACKUP_S3_SECRET_ACCESS_KEY`, and, if needed, `BACKUP_S3_SESSION_TOKEN`.
Store that file privately with owner-only permissions (`0600`) and never commit
it. Shell redirection sends its contents to stdin, keeping the credentials out
of command arguments and shell history; do not type the values into a command
or put them in `fly.toml`.

[`fly secrets import`](https://fly.io/docs/flyctl/secrets-import/) accepts
`NAME=VALUE` input on stdin. `--stage` only stages the secrets for a subsequent
deployment; omitting it deploys the secrets and restarts the app. The non-secret
`secrets set` command above deploys the staged credentials too and restarts the
machine; the entrypoint then takes its boot backup and the timer uploads it.

To trigger and watch the first upload by hand on the machine:
`python manage.py backup_scheduler --once --force`, then
`python manage.py backup_status`. Only `last_remote_success_at` going from
`None` to a timestamp, with a `last_remote_key`, means an object really landed.
Then do the restore drill below from the downloaded object before relying on
it.

## How uploads work and fail

`curl` (already in the image) does a SigV4-signed `PUT`
(`--aws-sigv4 aws:amz:<region>:s3`, see
<https://curl.se/docs/manpage.html#--aws-sigv4>) with the payload's SHA-256 in
`x-amz-content-sha256`, so S3 itself rejects a corrupted transfer. Every
snapshot is a new object `PREFIX/db-….sqlite3`; nothing is overwritten or
deleted remotely and no ACL header is sent (objects take the bucket's private
default).

- Credentials are fed to curl on **stdin** as a `--config -` file
  (<https://curl.se/docs/manpage.html#-K>), never in arguments, environment,
  URLs, logs, or the state file. curl runs with `-q` (ignores `~/.curlrc`) and a
  minimal environment (no proxies). HTTPS only, redirects are not followed,
  TLS ≥ 1.2, 10 s connect and `BACKUP_UPLOAD_TIMEOUT_SECONDS` total.
- Each configured value is checked against a strict allowlist before it can
  reach that config file; a hostile endpoint, prefix or secret is rejected
  instead of becoming an extra curl option. curl's stderr is redacted and
  truncated before it is stored or logged.
- After a successful local snapshot the file is added to `pending_uploads` in
  the state file. The timer uploads pending files oldest first. Success is only
  recorded after curl exits 0 **and** the server answers 2xx
  (`last_remote_success_at`, `last_remote_key`, `last_remote_sha256`).
- On failure the file **stays pending**: nothing off-site is claimed, local
  retention will not delete it even beyond `BACKUP_KEEP_LOCAL` (at most 14
  pending files are protected, newest first, so a months-long outage cannot
  fill the volume), and uploads retry with the same backoff as local failures.
  The local backup is unaffected.

### Retention

- **Local:** the newest `BACKUP_KEEP_LOCAL` snapshots (default 7) plus anything
  still waiting for upload. Boot backups count too, so several deploys in a day
  push older daily snapshots out; the off-site copy is what keeps history.
- **Off-site:** the app never deletes remote objects (its key should not be able
  to). Set a **bucket lifecycle rule** yourself, for example expire objects under
  the prefix after 30–90 days, optionally transition to a cheaper storage class,
  and enable versioning/Object Lock if you want protection against a
  compromised key.

## Recovery

| Symptom | Check | Fix |
|---|---|---|
| `backup_status --check` fails with "local backup is overdue" | `last_local_error`, `local_failures`, disk space on `/data`, scheduler running (`ps`)? | Fix the cause; the next check (≤60 s) retries. `backup_scheduler --once --force` retries now. |
| Off-site `remote: misconfigured` | `remote_config_error` names the setting | Correct the secret/env and restart the machine. |
| `remote_failures` rising, `last_remote_error` says `curl exited with status 6/7/28` (DNS/connect/timeout) or `22` (HTTP error) | Endpoint, region, bucket, key permissions, clock skew (SigV4 rejects a skewed clock) | Fix and wait for the backoff, or `backup_scheduler --once`. Pending files are still on disk. |
| Scheduler not running | Boot log shows `WARNING: backup scheduler exited abnormally`, or no `Backup scheduler started` line | It restarts with the machine; check the log line before it for the reason. |
| State file damaged | The unreadable file is moved to `backup-state.json.corrupt`; the timer starts fresh and takes a backup | Pending-upload bookkeeping is lost: run `backup_scheduler --once --force` after confirming the remote works. |

## Restore drill

Run this on a schedule (and always after first enabling off-site). It never
touches the live database: the target must be a **new** path, and the tool
refuses an existing file, the configured database (including through a
symlink), and the snapshot itself.

1. Get a snapshot: the newest local one is used by default; for the off-site
   copy download an object with your provider's own tool (console, `aws s3 cp`,
   …) to a scratch location. Compare `sha256sum <file>` with
   `last_remote_sha256` from `backup_status`.
2. Restore it into a scratch database, e.g. on a local machine from a fetched copy:

       DJANGO_DB_PATH=/path/to/scratch/dev.sqlite3 python3 manage.py \
         restore_backup_drill /path/to/scratch/restore-drill.sqlite3 --backup /path/to/db-….sqlite3

   It prints `integrity_check: ok`, the foreign-key violation count, and a row
   count per table, and exits non-zero if `PRAGMA integrity_check` fails. A
   file SQLite cannot read at all also fails loudly and leaves no scratch file.
3. Compare the counts (users, decks, cards, reviews) with what you expect, then
   point a throwaway server at it to look around:
   `DJANGO_DB_PATH=/path/to/scratch/restore-drill.sqlite3 python3 manage.py runserver 127.0.0.1:8000`.
4. Delete the scratch file. To actually recover production, replacing
   `/data/db.sqlite3` is a deliberate manual step (stop the app, keep the
   damaged file aside, copy a verified snapshot into place), never something
   the tools above do.
