# Troubleshooting tree

The operator troubleshooting tree. [`tree.yaml`](tree.yaml) is the single source of truth
(schema in [`schema.md`](schema.md)); it drives BOTH the generated printable
[`troubleshooting_guide.md`](troubleshooting_guide.md) (via
`scripts/build_troubleshooting_guide.py`) and the dashboard `/troubleshoot` view. Coverage is
enforced by `tests/test_troubleshooting_tree.py` (every daemon, watchdog check, HELD state, and
runbook must be covered or exempted). After editing `tree.yaml`, regenerate the guide and
re-record its `sha256` in `docs/enablement/manifest.yaml`.

<!-- BEGIN AUTO-INDEX -->
| Date | Type | Status | Workstream | Title | PRs |
|------|------|--------|------------|-------|-----|
| 2026-07-15 | reference | active | docs | [Troubleshooting Tree — Schema](schema.md) | _–_ |
| 2026-07-15 | reference | active | docs | [ITS Troubleshooting Guide](troubleshooting_guide.md) | _–_ |
<!-- END AUTO-INDEX -->
