# Smartsheet Migration Toolkit

Pulls Evergreen Renewables' existing chaos-workspace data into the canonical
ITS Smartsheet filing structure (Forefront Portfolio — ITS Demo,
workspace `4129485730670468`). Built and proved against Bradley 1 in a
2026-05-16 session; this is the canonical pattern Customer 2+ will reuse.

## Status

| Migration                              | Status | Rows |
|----------------------------------------|--------|------|
| Bradley 1 / Schedule                   | ✓      |   53 |
| Bradley 1 / Closeout — Exhibit K-1     | ✓      |   92 |
| Bradley 1 / Financial Ledger           | ✓      |  292 |
| Bradley 1 / Buyouts (Subs / Materials / Equipment) | pending | |
| Ops DB seed                            | ✓      | 28 + 10 |
| DFR backfill                           | pending |     |

## Pattern

For each source → dest migration:

1. **inspect** the source sheet (columns, picklist values, hierarchy).
2. **dry-run** the transform — print the rows that *would* be written.
3. **write** with an idempotency guard (abort if dest already has rows).
4. **verify** counts and spot-check rows in the Smartsheet UI.

## Usage

```bash
# Activate the repo venv (stdlib-only, but keeps Python pinned).
source ~/its/.venv/bin/activate

# Token from Smartsheet → Personal Settings → API Access.
export SMARTSHEET_TOKEN=...

cd ~/its/smartsheet_migration

# Sanity-check the token + see your user.
python3 ss_api.py whoami

# Schedule migration.
python3 inspect_source_schedule.py
python3 migrate_schedule_dryrun.py   # dry-run first
python3 migrate_schedule.py          # idempotency-guarded real write

# Closeout K-1 migration.
python3 inspect_closeout.py
python3 classify_closeout.py
python3 migrate_closeout.py          # idempotency-guarded real write

# Financial Ledger migration (Bradley 1).
python3 migrate_fl.py --mode dry     # parse + emit, no writes
python3 migrate_fl.py --mode sample  # write Valmont block only
python3 migrate_fl.py --mode full    # write all of Bradley 1

# Vendor DB + Subcontractor DB seeding from Bradley 1 FL parse.
python3 seed_ops_dbs.py --mode dry
python3 seed_ops_dbs.py --mode seed  # idempotency-guarded real write

# Create the human-review sheets in 06 — Human Review.
python3 build_human_review.py
```

## Files

| File                              | Purpose |
|-----------------------------------|---------|
| `ss_api.py`                       | REST helper (the column-ID workaround). Also a CLI: `whoami`, `columns`, `rename-folder`, `get-sheet`. |
| `inspect_source_schedule.py`      | Survey Bradley 12.8.25 source schedule. |
| `migrate_schedule_dryrun.py`      | Schedule transform — print only. |
| `migrate_schedule.py`             | Schedule transform — real write, with predecessor remap. |
| `inspect_closeout.py`             | Survey Portfolio Closeout source, Bradley rows + picklists. |
| `classify_closeout.py`            | Classify each source row: master / section / subsection / deliverable. |
| `migrate_closeout.py`             | Closeout K-1 transform — real write, 3-level hierarchy + 6 normalization rules. |
| `migrate_fl.py`                   | Financial Ledger transform — flat one-row-per-event ledger; unfolds overloaded source rows (Contract/CO/Invoice/Payment). `--mode dry|sample|full`. |
| `seed_ops_dbs.py`                 | Seed Vendor DB + Subcontractor DB from the Bradley 1 FL parse. Reuses `migrate_fl.find_blocks()`. `--mode dry|seed`. |
| `build_human_review.py`           | Provision `WPR_Pending_Review` and `ITS_Review_Queue` in 06 — Human Review. |

## Reference

See **ITS Smartsheet Handoff v4** in the Claude.ai planning project for the
full handoff: workspace map, column-ID resolution story, per-sheet mapping
decisions, and the Q1–Q5 normalization rules used in `migrate_closeout.py`.

## Future

- **Merge with `shared/smartsheet_client.py`** once Financial Ledger,
  Buyouts, Ops DB seed, and DFR backfill are written and the common API
  surface is obvious. Premature framework now = rewrite later. Target: ≥4
  real migrations before lifting `ss_api.py` into `shared/`.
- **Credential source.** This toolkit reads `SMARTSHEET_TOKEN` from env —
  a deliberate deviation from CLAUDE.md's Keychain-only policy
  (`shared.keychain.get_secret`). The deviation lives here until the merge
  above; at that point credentials move to Keychain.
