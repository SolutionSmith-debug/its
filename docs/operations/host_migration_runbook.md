---
type: operations
date: 2026-07-09
status: active
related_prs: []
workstream: infrastructure
tags: [aug7_delivery, host_migration, cutover, phase_1.5]
---

# Production-Host Migration Runbook (old MacBook Pro)

Phases A/B/C for migrating ITS off the development MacBook and onto the old
MacBook Pro that becomes the **production host** installed at Evergreen on
Aug 7. Program of record: `docs/2026-07-09_aug7_delivery_program.md` (WS4).
Calendar: **Phase A** provision Thu Jul 10 · **Phase B** one-way flip Mon
Jul 13 (~30-min ordered window) · **Phase C** burn-in Jul 14 → Aug 3.

Phase A facts below were verified live against the dev box + repo HEAD on
2026-07-09 — treat later re-runs per brief-validator discipline (re-verify
versions/paths before acting; zero grep hits beat confident memory).

## Purpose

Move the launchd daemon fleet, Keychain secrets, and repo checkouts to the
production host with **zero double-run window** and observable, mechanically
verified end-state — then burn the host in for ≥10 days before the Aug-3
tenant cutover lands on the same machine (no second migration).

## Hazards — read BEFORE touching either machine

1. **Keychain `security … -w` TTY trap.** With a controlling terminal,
   `security` PROMPTS on `/dev/tty` and **ignores piped stdin** (this corrupted
   the Box refresh token twice). Two safe forms ONLY:
   - *Interactive seeding (preferred for manual re-seed):*
     `security add-generic-password -a "$USER" -s <NAME> -w`
     — bare `-w` at the END, no value: it prompts twice, and the secret never
     enters shell history.
   - *Scripted seeding:* `security add-generic-password -U -a "$USER" -s <NAME> -w '<VALUE>'`
     — **`-U` (update-in-place) must come BEFORE `-w VALUE`**; placed after,
     it is swallowed as part of the password.
   - **NEVER pipe a value into bare `-w`.** It reads the terminal, not the pipe.
2. **Box single-host rule — zero Box secrets in Phase A.** The Box refresh
   token rotates on EVERY exchange and is single-consumer: two hosts holding
   it fight, and the loser dies `invalid_grant`. `ITS_BOX_CLIENT_ID` /
   `ITS_BOX_CLIENT_SECRET` / `ITS_BOX_REFRESH_TOKEN` are **excluded from the
   Phase-A re-seed**. Box moves ONLY in Phase B, via a fresh
   `scripts/setup_box_oauth.py` run **on the new host**, after the dev box is
   verified out of the daemon business.
3. **`install.sh` has NO install-without-load mode, and `~/Library/LaunchAgents`
   auto-bootstraps at login.** `install.sh load` renders, lints, AND
   `launchctl bootstrap`s in one step; even a hand-copied plist in
   `~/Library/LaunchAgents/` would come alive at the next login. Therefore
   **Phase A does not install plists at all** — it only renders + lints via
   `scripts/launchd/install.sh dry-run <name>` and verifies no `__…__`
   placeholder survives. The actual `load` of the 14 (all but the send-gated `po-send`) happens in **Phase B**,
   AFTER the dev box is verified empty.
   > **Program-doc amendment (explicit):** the program doc's Phase-A line
   > "plists installed UNLOADED" is **amended by this runbook** — there is no
   > such mode. Phase A = dry-run render/lint only; Phase B = load.

## Procedure — Phase A: provision (Thu Jul 10, dev box stays live)

Everything in Phase A is additive and read-only against live services. The
dev box keeps running all daemons throughout.

### A1 — macOS baseline

1. **FileVault decision (owner sign-off, record the choice here or in the
   session log):**
   - **ON** → disk encrypted, but auto-login cannot cross the FileVault
     unlock: **every reboot needs a human** at the machine before daemons
     resume. Document as reboot-needs-human.
   - **OFF** → unattended reboots work end-to-end; requires explicit owner
     sign-off on the unencrypted-disk posture.
2. Enable auto-login for the ITS user (System Settings → Users & Groups) —
   moot across reboots if FileVault is ON, still required for power-restore.
3. Power posture (verify with `pmset -g`):

   ```bash
   sudo pmset -a sleep 0 disksleep 0 disablesleep 1 autorestart 1
   ```

4. macOS automatic updates **OFF** (System Settings → General → Software
   Update → Automatic updates): an unattended surprise-reboot mid-cycle is a
   Tier-1 event we do not schedule. OS updates become deliberate operator
   maintenance windows.

### A2 — toolchain (dev-box-verified targets)

Install and verify each (targets verified on the dev box 2026-07-09):

| Tool | Target | Verify |
|------|--------|--------|
| Xcode CLT | current | `xcode-select -p` |
| Homebrew | current | `brew --version` |
| Python | **3.13.x** (match dev box) | `python3 --version` |
| node | **v26.x** | `node --version` |
| git | **2.50+** | `git --version` |
| gh | **2.9x** | `gh --version`; then `gh auth login` + `gh auth status` |
| wrangler | via `npx wrangler` (repo-local install; publish_daemon is the §50 actuator) | `cd ~/its/safety_portal && npx wrangler --version` |
| **Tailscale — NET-NEW** (not on the dev box) | current | install, join the tailnet, `tailscale status`; Tailscale-only remote access (no public SSH — CLAUDE.md "What NOT to do") |
| **ClamAV + freshclam — NET-NEW** | current | `brew install clamav`; configure + `freshclam`; enablement + EICAR prove-it-bites belong to the **hardening gate** (program §4.3), not Phase A |

### A3 — repos as `~/` siblings (symlink integrity is load-bearing)

```bash
cd ~
git clone git@github.com:SolutionSmith-debug/its.git its
git clone git@github.com:SolutionSmith-debug/its-blueprint.git its-blueprint
```

The blueprint's `.claude/hooks` + `.claude/agents` entries are **relative
symlinks into `../../its/...`**. A non-sibling layout does not error — the
symlinks dangle and the guard hooks **silently drop, fail-open**. Verify:

```bash
ls -l ~/its-blueprint/.claude/hooks ~/its-blueprint/.claude/agents
# every symlink target must resolve; zero output from:
find -L ~/its-blueprint/.claude -type l
```

(`find -L … -type l` prints only BROKEN symlinks — any output is a fail.)

### A4 — venv + provision gate

```bash
cd ~/its
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q          # expect: green (integration tests auto-deselected)
.venv/bin/mypy .             # expect: 0 errors
.venv/bin/ruff check .       # expect: clean
```

All three green = the host can run ITS code. Any failure here is a
provisioning defect — fix before proceeding (do not carry a red gate into
Phase B).

### A5 — Keychain re-seed: the 11 non-Box secrets (+ 4 pending)

Seed each with the interactive form (Hazard 1):

```bash
security add-generic-password -a "$USER" -s ITS_SMARTSHEET_TOKEN -w
```

| # | Service name | Notes |
|---|--------------|-------|
| 1 | `ITS_SMARTSHEET_TOKEN` | Smartsheet API |
| 2 | `ITS_ANTHROPIC_KEY` | Anthropic API |
| 3 | `ITS_RESEND_API_KEY` | operator-alert email leg |
| 4 | `ITS_SENTRY_DSN` | CRITICAL capture leg |
| 5 | `ITS_MS_TENANT_ID` | Graph (M365) |
| 6 | `ITS_MS_CLIENT_ID` | Graph (M365) |
| 7 | `ITS_MS_CLIENT_SECRET` | Graph (M365) |
| 8 | `ITS_PORTAL_INTERNAL_TOKEN` | portal_poll bearer |
| 9 | `ITS_PORTAL_HMAC_SECRET` | portal HMAC verify |
| 10 | `ITS_PORTAL_ADMIN_TOKEN` | portal admin CLI |
| 11 | `ITS_PORTAL_FIELDOPS_TOKEN` | fieldops_sync bearer |
| — | `ITS_PORTAL_PO_TOKEN` | **PENDING** — seed once the operator provisions it (WS1 S2); required by cutover day (`verify_cutover` VC-01 names it until then) |
| — | `ITS_PORTAL_CONFIG_TOKEN` | **PENDING** — config-actuator (§50) daemon bearer; loaded-but-runtime-dark, required by cutover (VC-01) |
| — | `ITS_PORTAL_SUB_TOKEN` | **PENDING** — subcontract-poll daemon bearer; loaded-but-runtime-dark, required by cutover (VC-01) |
| — | `ITS_OPERATOR_PIN` | **PENDING** — operator-dashboard ACT-surface PIN (manual-start, no plist); required by cutover (VC-01) |

**Box triplet deliberately absent** (Hazard 2). Total VC-01 required = **18** (11
core non-Box seeded here + Box triplet in Phase B + 4 pending above).

Verify loop — presence + plausible length, values never printed:

```bash
cd ~/its && .venv/bin/python - <<'EOF'
from shared import keychain
NAMES = [
    "ITS_SMARTSHEET_TOKEN", "ITS_ANTHROPIC_KEY", "ITS_RESEND_API_KEY",
    "ITS_SENTRY_DSN", "ITS_MS_TENANT_ID", "ITS_MS_CLIENT_ID",
    "ITS_MS_CLIENT_SECRET", "ITS_PORTAL_INTERNAL_TOKEN",
    "ITS_PORTAL_HMAC_SECRET", "ITS_PORTAL_ADMIN_TOKEN",
    "ITS_PORTAL_FIELDOPS_TOKEN",
]
for name in NAMES:
    try:
        print(f"{name}: len={len(keychain.get_secret(name))}")
    except Exception as exc:
        print(f"{name}: MISSING ({type(exc).__name__})")
EOF
```

Every line must print a non-zero `len=`; compare lengths against the same
loop run on the dev box (a truncated paste shows up as a length mismatch).

### A6 — launchd render + lint ONLY (no load — Hazard 3)

For each of the 15 daemon plists in `scripts/launchd/`:

```bash
cd ~/its/scripts/launchd
for p in org.solutionsmith.its.*.plist; do
  name="${p%.plist}"
  ./install.sh dry-run "$name" | grep -n "__" \
    && echo "FAIL: unresolved placeholder in $name" \
    || echo "ok: $name renders clean"
done
```

Any surviving `__…__` placeholder (outside XML comments) is a fail —
escalate to Seth (a plist/installer change is a code change, §44 high-class).
Do NOT `install.sh load` anything, and do NOT hand-copy plists into
`~/Library/LaunchAgents/` — they auto-bootstrap at the next login.

launchd facts that shape operations on this host (from
`scripts/launchd/README.md`):

- LaunchAgents run in **user context** — the **login keychain must be
  unlocked**; after a reboot, daemons run only once the user has logged in.
  (With FileVault ON this is the reboot-needs-human path from A1.)
- Labels are `org.solutionsmith.its.<name>` through the build phase; they
  rename to `com.evergreenrenewables.its.<name>` at **Phase 1.5 post-handover**
  (plists regenerated then — not during this migration).

### A7 — read-only service smokes (no sends, no writes)

```bash
cd ~/its
# Smartsheet — read one config row:
.venv/bin/python -c "from shared import smartsheet_client as s; \
print(s.get_setting('system.heartbeat_url', workstream='global'))"
# Graph — read-only smoke:
.venv/bin/python scripts/smoke_test_graph.py
# Sentry DSN + Resend key shape (presence-only; NEVER fire a test alert here):
.venv/bin/python -m scripts.verify_cutover --only alerting
```

Box is intentionally not smoked (Hazard 2 — no Box credentials exist on this
host yet).

## Procedure — Phase B: one-way flip (Mon Jul 13, ~30-min ordered window)

The #1 program hazard is a **daemon double-run**: Box refresh-token rotation
is single-consumer, double polls double-write, and two watchdogs mask one
dead heartbeat. The order below is non-negotiable; do not parallelize.

1. **Dev box — unload all 15:**

   ```bash
   cd ~/its/scripts/launchd
   for p in org.solutionsmith.its.*.plist; do ./install.sh unload "${p%.plist}"; done
   ```

2. **Dev box — verify EMPTY:**

   ```bash
   launchctl list | grep solutionsmith   # MUST print nothing
   ```

3. **Dev box — verify plists REMOVED** (so the fleet stays dead across any
   future login/reboot of the dev box; `install.sh unload` deletes the
   installed copy — verify it):

   ```bash
   ls ~/Library/LaunchAgents/org.solutionsmith.its.* 2>/dev/null  # MUST print nothing
   ```

4. **Copy state to the new host** (dedupe state prevents an alert storm;
   markers keep Check C honest):

   ```bash
   rsync -av ~/its/state/ newhost:~/its/state/
   rsync -av ~/its/.watchdog/ newhost:~/its/.watchdog/
   ```

5. **New host — Box re-auth (the ONLY place Box secrets appear):** run
   `scripts/setup_box_oauth.py` fresh on the new host (seeds
   `ITS_BOX_CLIENT_ID` / `ITS_BOX_CLIENT_SECRET` / `ITS_BOX_REFRESH_TOKEN`).
   From this moment, **never run Box-consuming code on the dev box again** —
   the first refresh on the new host invalidates the dev box's token lineage.
6. **New host — bring the repo current, then load all daemons EXCEPT the dark-unloaded send daemon (`po-send`):**

   ```bash
   git -C ~/its pull origin main   # never load from a stale checkout
   cd ~/its/scripts/launchd
   for p in org.solutionsmith.its.*.plist; do
     name="${p%.plist}"
     [ "$name" = "org.solutionsmith.its.po-send" ] && continue   # send-gate: stays UNLOADED
     ./install.sh load "$name"
   done
   ./install.sh status             # 14 loaded (po-send UNLOADED — send-gate; subcontract-poll loaded, runtime-dark)
   ```

7. **Verification gates (all must pass before declaring the flip done):**

   | Gate | Command / observable |
   |------|----------------------|
   | Labels loaded | `python -m scripts.verify_cutover --only launchd` → PASS |
   | Fresh Check-C markers | `ls -l ~/its/.watchdog/*.last_run` — every tracked job's marker mtime advances past the flip time within its window |
   | ITS_Daemon_Health advancing | `python -m scripts.verify_cutover --only daemon-health` → PASS (mirror tenant: add `--allow-sandbox` to any `config` runs; `keychain` will name `ITS_PORTAL_PO_TOKEN` until it is provisioned) |
   | Portal round trip | test submission on the mirror portal → `portal_poll` pulls within ~60s → PDF filed in Box (mirror ROOT → job → week) |
   | **UptimeRobot prove-it-bites** | `./install.sh unload org.solutionsmith.its.watchdog` → wait 35 min → UptimeRobot alert ARRIVES → `./install.sh load org.solutionsmith.its.watchdog` → monitor returns green. A control that never fired is not a control. |

## Procedure — Phase C: burn-in (Jul 14 → Aug 3)

Per the program master calendar:

- **Jul 14–16** — passive burn-in; hardening-gate work happens beside it
  (ClamAV/EICAR, Paid-plan/PBKDF2 verdict — program §4.3).
- **Fri Jul 17** — burn-in Friday cycle #1 (`weekly_generate` 14:00 +
  `progress` compile): verify packets + review rows land unattended.
- **Fri Jul 24** — Friday cycle #2; **code freeze on daemon paths** after it.
- **Jul 25–30 (operator away)** — the gap IS the test: an unattended Tier-1
  live trial (mirror-only blast radius). Nothing merges to live.
- **Fri Jul 31** — gap telemetry review = host **go/no-go**: zero unexplained
  CRITICALs, Check-C markers continuous, UptimeRobot uninterrupted, dedupe
  summaries reviewed. The same host then receives the Aug-3 tenant cutover
  (`docs/operations/cutover_checklist.md`).

## §43 successor-remediation entries (symptom → repair → escalate)

| Symptom | Low-class repair (Tier 2) | Escalate to Seth when |
|---------|---------------------------|------------------------|
| A daemon shows `not loaded` in `install.sh status` after the flip | `cd ~/its/scripts/launchd && ./install.sh load <label>`; re-check `status` | `install.sh load` fails `plutil -lint`, or a `__…__` placeholder survives dry-run (plist/installer change = code change, high-class) |
| Daemons dead after a reboot | Log in at the machine (login keychain unlocks; LaunchAgents start); verify `install.sh status` | Daemons stay dead after login, or the keychain prompts for a password that doesn't work (secrets/auth = high-class) |
| Box calls failing `invalid_grant` after the flip | None — do not touch Box credentials | Immediately: refresh-token lineage is a secrets/auth repair (re-run `setup_box_oauth.py` is Seth's call) |
| UptimeRobot alert during burn-in with the host apparently up | Check `launchctl list \| grep solutionsmith`; if the watchdog label is missing, `install.sh load org.solutionsmith.its.watchdog` | Watchdog is loaded but the monitor stays red (config/network diagnosis) |
| Keychain read errors (`KeychainLockedError`) in daemon logs | `security unlock-keychain` after logging in at the machine | Errors persist while logged in |

## Validation

- Phase A done = A4 three-gate green + A5 verify loop 11/11 (the 11 core non-Box
  secrets; the 4 pending are provisioned separately) + A6 loop prints 15 `ok:`
  lines + A7 smokes green.
- Phase B done = step-7 table fully green, **including** the UptimeRobot
  prove-it-bites.
- Phase C done = Jul-31 go/no-go recorded (session log), zero unexplained
  CRITICALs across the gap.
- The tenant cutover then runs `python -m scripts.verify_cutover` (full, no
  `--allow-sandbox`) per `docs/operations/cutover_checklist.md`.

## Owner

`@solutionsmith` (Developer-Operator). Phase B is a Developer-Operator-only
window (Box re-auth = secrets/auth, §44 high-class); the §43 table above
covers the Successor-Operator's burn-in-period repairs.
