# Scheduled Scripts

Top-level scripts triggered by launchd or Mail.app rules. Each script is a thin entry point:
load context, dispatch into a workstream module, write back results.

- `watchdog.py` — daily health check (cross-cutting; not tied to a single workstream).
