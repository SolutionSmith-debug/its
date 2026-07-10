"""ITS Operator Dashboard — WS2 D1-1 (read-only core).

A loginless, localhost-only (127.0.0.1:8484) FastAPI app that OBSERVES the
live ITS daemon tree at ~/its: launchd status, watchdog markers, the
Smartsheet circuit breaker, daemon heartbeats, state locks, the recent log
tail, and TTL-cached Smartsheet read panels (ITS_Errors, ITS_Review_Queue).

By construction it has ZERO write, act, or send capability — every route is
GET, no daemon is edited, no plist is installed, no Keychain PIN or send
path exists. The Tier-2 action surface (§44) is D1-2; this slice is the
read-only floor it will build on without refactor.
"""
