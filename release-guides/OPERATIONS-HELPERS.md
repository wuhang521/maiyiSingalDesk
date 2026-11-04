# Reviewed health operations helpers

These helpers are source deliverables for Root's managed-host review. Their
default mode is a plan. Their local tests use temporary synthetic files and
mock privileged/systemd operations; they do not prove server deployment,
Linux permissions, a real calendar trigger or a visible client notification.

## Two-file collector update

`scripts/apply-health-collector-update.py` accepts only a new archive under
`/var/tmp/signaldesk-health-collector-*.tar.gz` (or `.tgz`). Build the archive
with exactly two ordinary USTAR members and integer metadata, without PAX,
directories or links: `app/health_delivery.py` and `app/health_collector.py`.
Supply the reviewed archive SHA and both Root-frozen after SHAs. The old worker
must be exactly9c7a8995c26456aef4cab1d24b9e5f9bed490a1735279a4a9fffc0f8bb20138d;
the new collector must be absent. There is no target/before-image override.

```text
/opt/signaldesk-task/.venv/bin/python /var/tmp/apply-health-collector-update.py \
  --archive /var/tmp/signaldesk-health-collector-REVIEWED.tar.gz \
  --archive-sha256 ROOT_ARCHIVE_SHA \
  --worker-sha256 ROOT_WORKER_AFTER_SHA \
  --collector-sha256 ROOT_COLLECTOR_AFTER_SHA
```

Add `--apply` only for the reviewed write. It compiles the two public byte
payloads inside Task Python3.10 without importing/executing them, locks the old
worker inode, creates a unique root0700 backup with old bytes/metadata0600,
installs the collector first and atomically replaces the worker. File and
directory fsync are part of the actual POSIX path. The worker retains original
uid/gid/mode/mtime. A failed transaction restores only its exact after-images;
external third images remain for review. A restored file has a new inode.
No DB, env, migration, timer or service operation is performed.

Local tests: `scripts/test_apply_health_collector_update.py`,17 PASS. Windows
tests mock ownership, directory fsync, flock and the Task subprocess. The latter
tests the fixed compile command contract, not an actual local Task3.10 runtime.

## Temporary real-clock timer acceptance

`scripts/install-health-timer-acceptance.py` fixes state to a unique
`/var/lib/signaldesk-health-timer-acceptance-<hyphen-run-id>` and uses only the
existing private health config. It stages the reviewed public two-module bytes;
it never copies a private generator, env or old snapshot into that stage.

```text
/opt/signaldesk-task/.venv/bin/python /var/tmp/install-health-timer-acceptance.py \
  --mode install --run-id timer-UNIQUE-LOWERCASE-HYPHEN-ID \
  --worker-sha256 ROOT_WORKER_AFTER_SHA \
  --collector-sha256 ROOT_COLLECTOR_AFTER_SHA
```

The example run-id is a placeholder: use lowercase letters/digits/hyphens.
Add `--apply` for the reviewed install. Preparation runs as UID994/GID990 with
one explicit synthetic item, a ClosedTransport and blocked socket connections;
it cannot ingest or publish. The single article's test-prefixed title equals
the ntfy title; body/source/click are verified by the bundled JS parser locally.

After preparation the helper calculates a real Shanghai instant at least120
seconds ahead. The target must remain on the same day at/after09:00 because the
actual Worker keeps its delivery gate. It requires systemd219, Asia/Shanghai
and NTP synchronization, then writes only its unique service/timer. Typed D-Bus uint64 is used for monotonic trigger time; the human-formatted systemctl219 timespan is never converted by guessing month lengths. The service
uses UID994/GID990, the existing Task EnvironmentFile and actual Worker
Transport. The calendar has1second accuracy. Only the test timer is started,
using `systemctl start --no-block`; no formal timer is enabled or changed.

After the actual timer fires, run `--mode inspect --run-id <id>`. Only an actual
past calendar/LastTriggerMonotonic, successful service and one sent row/ACK
permit `--mode second-deliver --run-id <id> --apply`. Inspect again after the
second service completes. `phase:verified,passed:true` requires the same ACK,
one row/attempt and zero additional ingest/publish calls. These are real-host
requirements, not satisfied by the helper's synthetic local tests.

The JSON schema is `signaldesk.health-timer-acceptance.v1`. `test_run:true`
identifies the labeled synthetic article. `synthetic_only:false` describes
actual host/systemd/Transport observation, not genuine news content. Root must
separately capture the visible client receipt; this helper cannot assert it.
Output contains unit names, instants, hashes, counts and ACK hashes only.
Before redirecting it into a private evidence file, set the shell umask077;
process-local umask cannot protect an already opened shell redirection.

`--mode cleanup --run-id <id> --apply` stops/removes only the fixed unique test
units after persisted inode/device/owner/mode/hash checks and keeps all private
state, snapshots and ledger. A failed test stays failed. Preserve a final inspect
receipt before removing its units. Same-content replacement or changed unit
permissions are retained for review. Local test log: scheduler/timer-helper-tests.log
in the separate evidence bundle;24 synthetic PASS, including actual Worker
SQLite/ACK dedupe and one local Node parser case. All systemd/identity/privileged
file operations are mocked locally. No server timer, public message, user
profile or UI was operated by these local tests.

Formal activation uses the separately reviewed
`scripts/activate-health-schedule.py` and the activation guide in this source
bundle. It requires independently produced real timer and client evidence;
none is manufactured by packaging or the synthetic test helpers.

