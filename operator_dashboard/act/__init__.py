"""The D1-2 ACT surface: Class-A runtime config edits.

The FIRST mutation surface of the operator dashboard. It writes ONLY to
ITS_Config (an internal system-of-record write, not an external send — the
External Send Gate stays owned by the daemons), guarded by the PIN + Origin
allowlist (operator_dashboard.auth), validated per-key (validators.registry),
first-activation-escalated for send-poller gates, and audited on every write.
"""
