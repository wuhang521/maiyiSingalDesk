# Health schedule activation helper — prepared, not activated

`D:/maiyi/tmp/health-push-064-src/scripts/activate-health-schedule.py` is a server-local helper. Server SSH was used only to stage two versioned public helpers and perform read-only plan/state checks. This agent did not activate timers, send messages, change real configuration, run migrations, or operate UI. The default mode is a read-only plan. A successful plan with missing evidence is explicitly `activation_ready:false`; it is not production acceptance.

Local verification: **59 tests passed**, including real isolated probe subprocesses, Python AST syntax checked. The tests use actual temporary NTFS files and mock every systemctl, network, producer/config adapter, and production path. They do not demonstrate real systemd operation, Linux UID/GID/mode, message delivery, or visible client behavior. Test log: [synthetic-tests.log](synthetic-tests.log). The initial sandbox run had 28 passed/6 failed because Windows denied same-directory hard links (WinError 5); an approved execution of the same isolated tests passed 34/34. Subsequent new cases passed 37/37 and 42/42, then final contract-aligned 44/44. No product behavior was weakened to bypass that file-system restriction.

The helper fixes all destinations under `/opt/signaldesk-task`, `/opt/signaldesk`, `/etc/systemd/system`, and the health-only private state/backup locations. There is no destination override. It verifies the four deployed formal unit hashes against the reviewed core, the private producer `src/main.py` hash `b704ec9cced3c0da0519591ffb400b3e27c10721f79a18789029b27ed87e3697`, and Root's hash-bound after manifest containing exactly:

```json
{
  "app/health_delivery.py": "ROOT_FROZEN_64_LOWERCASE_HEX_SHA256",
  "app/health_collector.py": "ROOT_FROZEN_64_LOWERCASE_HEX_SHA256"
}
```

The placeholders are intentionally invalid. Root must freeze the actual two after-image hashes after the reviewed collector update; the helper also hashes the deployed files. Private producer/config values stay on the server. A read-only authenticated loopback channel query and the existing producer configuration contract check require one managed canonical `health`, only alias `demo-health`, no duplicate destination, active/API-read state, `09:00`, max 100, and matching destination hashes. A producer EnvironmentFile override of the managed guard, including an explicit empty value, is rejected. The host must report `Asia/Shanghai` and `NTP synchronized: yes`; `HEALTH_START_DATE` must be `2026-10-31`.

For a plan, invoke the reviewed script with the Task interpreter and Root's public after manifest:

```text
/opt/signaldesk-task/.venv/bin/python /var/tmp/activate-health-schedule.py \
  --after-manifest /var/tmp/signaldesk-health-after.json \
  --after-manifest-sha256 ROOT_FROZEN_MANIFEST_SHA256
```

Before activation, provide the final real timer evidence and the separately reviewed client evidence:

```text
  --timer-evidence /var/lib/signaldesk-health-timer-acceptance-UNIQUE/acceptance.json \
  --timer-evidence-sha256 ROOT_FROZEN_TIMER_EVIDENCE_SHA256 \
  --client-receipt /var/tmp/health-client-receipt.json \
  --client-receipt-sha256 ROOT_REVIEWED_CLIENT_EVIDENCE_SHA256 \
  --client-ui-reviewed
```

Add `--activate` only for Root's approved activation execution. The client evidence may instead be inside the same private timer acceptance directory. Supplying its SHA and `--client-ui-reviewed` is the operator's assertion of a separate human review. The tool always reports `client_ui_automatically_verified:false`.

The timer evidence contract is `signaldesk.health-timer-acceptance.v1`, with `phase:verified`, `passed:true`, `test_run:true`, `clock_override:false`, non-synthetic runtime evidence, verified Shanghai/NTP, and its unique private `/var/lib/signaldesk-health-timer-acceptance-*` directory. Required data:

- `scheduled_at`: timezone-aware ISO instant in the past; `timer.last_trigger_monotonic_usec > 0`; `first_delivery.monotonic_usec` at or after that trigger.
- `service.result:success`, `service.exec_main_status:0`.
- Both `first_delivery` and `second_delivery`: `state:sent`, `articles`, `batch_rows`, `ack_count`, and `attempts_total` all 1, and identical valid `ack_sha256`.
- First `ingest_calls/publish_calls` 1/1; second 0/0; `dedupe_verified:true`.
- Exact current two-member `source_sha256` plus three `artifact_sha256` keys `runner`, `unit`, `timer`.

The one labeled test item may be synthetic content; the required trigger, Task row, Transport ACK and rerun measurements must be real server observations. The evidence is supplied and hash-frozen by Root after the separate timer helper runs; this script does not manufacture it.

Activation creates only an absent `signaldesk-worker.service.d/health-managed.conf`, or accepts that exact already-present root-owned 0644 file:

```ini
[Service]
Environment=SIGNALDESK_HEALTH_MANAGED=1
```

A root-owned 0700 backup records the original absent/exact drop-in and enabled/active states; files are 0600. New drop-in creation uses an atomic no-replace hard link, so a concurrently created foreign file is retained. The transaction daemon-reloads, verifies the managed directive, restarts the existing worker, enables both timers, then starts both timers in separate v219-compatible calls. It does not use `enable --now`. Re-running an already verified active deployment performs no worker restart, backup, or file write. Actual next calendar times are read from typed D-Bus `Timer.TimersCalendar` realtime fields, matching 08:15 and 09:00; the delivery timer's two-minute monotonic retry is never presented as its daily calendar.

On failure it restores owned intermediate timer states and the original drop-in, then restarts the original legacy worker. File after-image conflicts and unowned timer state images are retained and reported for review. It never restores Task env/source, topics, channel data, or business DB contents. Cleanup removes only an identity-checked newly created empty drop-in directory, never recursively. Before the cutover date it checks that the fixed private health ledger files remain unchanged; it cannot undo an external message already sent. Activation date/due state is explicit in metadata.

Tests cover plan/no writes, five partial systemctl failures, ordinary and existing-exact drop-in recovery, failure after atomic creation, foreign after-image preservation, frozen source/config changes, evidence write failure, cutoff ledger drift, real-calendar parsing, NTP/timezone gates, ACK/rerun/source evidence rejection, client review/hash requirements, and EnvironmentFile guard conflicts. Actual server activation, actual calendar behavior and visible client receipt remain Root's separate acceptance steps.



The 2026-10-30 runtime preflight fixes are documented in [preflight-fix-20261030.md](preflight-fix-20261030.md). The repaired server plan passed; the final typed-calendar revision intentionally awaits the reviewed R3 client receipt and Root activation. Producer dotenv parsing now runs in the existing Task runtime, and v219 states come from loaded/active manager properties. No dependency was installed.


The typed calendar update uses the actual v219 a(sst) property and rejects unarmed/infinite timestamps, wrong date/time/specification and monotonic retries. See [typed-calendar-fix-20261030.md](typed-calendar-fix-20261030.md). Product app/server tests remain unchanged.

