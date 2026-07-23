"""§54 runtime secret / PII-leak backstop (its#340).

Feeds SYNTHETIC secrets/PII into a CRITICAL and asserts they are masked out of the three `error_log`
surfaces that EGRESS the Mac — the `ITS_Errors` Smartsheet row, the Resend operator email, and the
Sentry event — while the on-Mac local log file keeps them raw (the deliberate forensics carve-out).
Plus direct unit cases for `shared.redact.redact()` shapes, and a mechanical AST guard that a
migration script never prints/logs a value bound from `keychain.get_secret(...)`.

Never uses a real Keychain token — all secrets here are fabricated example strings. The fixtures are
cloned from `tests/test_error_log.py` (kept file-local for isolation). See `shared/redact.py` for the
"backstop, not a guarantee" framing.
"""
from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

import pytest

import shared.alert_dedupe as alert_dedupe_module
import shared.error_log as error_log_module
from shared import redact as redact_module
from shared.error_log import Severity, log
from shared.redact import redact

# ---- synthetic secrets / PII (fabricated — never a real credential) --------
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
SK_TOKEN = "sk-proj-ZZZ999topsecretpayload012345"
BEARER = f"Authorization: Bearer {SK_TOKEN}"
KV_SECRET = "client_secret=hunter2superlongsecretvalue"
EMAIL = "crew.member@example.com"


# ---- fixtures (cloned from tests/test_error_log.py, file-local for isolation) ----


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.error_log.LOG_DIR", tmp_path)
    return tmp_path


def _today_log(log_dir: Path) -> Path:
    return log_dir / f"{datetime.now():%Y-%m-%d}.log"


@pytest.fixture(autouse=True)
def add_rows_mock(mocker, monkeypatch):
    monkeypatch.delenv("ITS_ERROR_LOG_INFO", raising=False)
    error_log_module._in_smartsheet_write = False
    mock = mocker.patch("shared.error_log.smartsheet_client.add_rows")
    yield mock
    error_log_module._in_smartsheet_write = False


@pytest.fixture(autouse=True)
def send_alert_mock(mocker):
    error_log_module._in_resend_alert = False
    error_log_module._in_alert_critical = False
    mock = mocker.patch("shared.resend_client.send_alert")
    yield mock
    error_log_module._in_resend_alert = False
    error_log_module._in_alert_critical = False


@pytest.fixture(autouse=True)
def alert_dedupe_state(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setattr(alert_dedupe_module, "STATE_DIR", state_dir)
    monkeypatch.setattr(alert_dedupe_module, "STATE_FILE", state_dir / "alert_dedupe.json")
    return state_dir


@pytest.fixture(autouse=True)
def sentry_capture_mock(mocker):
    error_log_module._in_sentry_capture = False
    mock = mocker.patch("shared.sentry_client.capture_exception")
    yield mock
    error_log_module._in_sentry_capture = False


def _row(add_rows_mock) -> dict:
    _sheet, rows = add_rows_mock.call_args.args
    assert len(rows) == 1
    return rows[0]


# ---- the three EGRESS surfaces of the triple-fire are redacted --------------


def test_secret_in_message_masked_from_its_errors_row(log_dir, add_rows_mock):
    log(Severity.CRITICAL, "s", f"boom with key {AWS_KEY}", exc_info="tb")
    row = _row(add_rows_mock)
    assert AWS_KEY not in row["Message"]
    assert "<redacted>" in row["Message"]


def test_secret_in_exc_info_masked_from_its_errors_traceback(log_dir, add_rows_mock):
    log(Severity.CRITICAL, "s", "boom", exc_info=f"Traceback...\n token={AWS_KEY}")
    row = _row(add_rows_mock)
    assert AWS_KEY not in row["Traceback"]
    assert "<redacted>" in row["Traceback"]


def test_secret_masked_from_resend_subject_and_body(log_dir, send_alert_mock):
    log(Severity.CRITICAL, "s", f"boom {AWS_KEY}", exc_info=f"tb {SK_TOKEN}")
    subject, body = send_alert_mock.call_args.args
    assert AWS_KEY not in subject and AWS_KEY not in body
    assert SK_TOKEN not in body
    assert "<redacted>" in body


def test_secret_masked_from_sentry_message_and_exc_info(log_dir, sentry_capture_mock):
    log(Severity.CRITICAL, "s", f"boom {AWS_KEY}", exc_info=f"tb {SK_TOKEN}")
    _script, message, exc_info = sentry_capture_mock.call_args.args
    assert AWS_KEY not in message
    assert SK_TOKEN not in exc_info
    assert "<redacted>" in message and "<redacted>" in exc_info


def test_bearer_and_kv_secret_masked_across_all_three_egress_legs(
    log_dir, add_rows_mock, send_alert_mock, sentry_capture_mock
):
    log(Severity.CRITICAL, "s", f"auth {BEARER}", exc_info=KV_SECRET)
    row = _row(add_rows_mock)
    _s, body = send_alert_mock.call_args.args
    _sc, s_msg, s_tb = sentry_capture_mock.call_args.args
    for surface in (row["Message"], row["Traceback"], body, s_msg, s_tb):
        assert SK_TOKEN not in surface  # the bearer token value
        assert "hunter2superlongsecretvalue" not in surface  # the kv value
    # the key + separator survive, only the value is masked
    assert "client_secret=<redacted>" in row["Traceback"]


def test_email_pii_masked_across_all_three_egress_legs(
    log_dir, add_rows_mock, send_alert_mock, sentry_capture_mock
):
    log(Severity.CRITICAL, "s", f"failed for {EMAIL}", exc_info=f"user {EMAIL}")
    row = _row(add_rows_mock)
    _s, body = send_alert_mock.call_args.args
    _sc, s_msg, _tb = sentry_capture_mock.call_args.args
    for surface in (row["Message"], row["Traceback"], body, s_msg):
        assert EMAIL not in surface
        assert "<redacted-email>" in surface


# ---- the on-Mac LOCAL log file is deliberately NOT redacted (forensics) -----


def test_local_log_file_keeps_secret_raw_for_forensics(log_dir, add_rows_mock):
    # §54 scopes the guarantee to the egress triple-fire; the on-Mac local file stays full-fidelity
    # so an operator can diagnose + rotate a leaked credential. This locks that intentional design.
    log(Severity.CRITICAL, "s", f"boom {AWS_KEY}", exc_info=f"tb {AWS_KEY}")
    contents = _today_log(log_dir).read_text()
    assert AWS_KEY in contents  # raw on-Mac; redaction applies only to the egress surfaces


# ---- redact() unit — shape precision (mask real shapes, don't over-redact) --


@pytest.mark.parametrize(
    ("raw", "must_go"),
    [
        (f"key={AWS_KEY} tail", AWS_KEY),
        (BEARER, SK_TOKEN),
        (f"tok {SK_TOKEN}", SK_TOKEN),
        ("xoxb-123456789012-abcdefghijkl", "xoxb-123456789012-abcdefghijkl"),
        ("ghp_ABCDEFghijkl0123456789", "ghp_ABCDEFghijkl0123456789"),
        ("github_pat_11ABCDEFG0123456789_zyxwvutsrqponmlkjih", "github_pat_11ABCDEFG0123456789_zyxwvutsrqponmlkjih"),
        ("resend re_AbCdEf0123456789AbCdEf0123", "re_AbCdEf0123456789AbCdEf0123"),
        ("dsn https://abc123def456ghi789@o42.ingest.sentry.io/789", "https://abc123def456ghi789@o42.ingest.sentry.io/789"),
        (KV_SECRET, "hunter2superlongsecretvalue"),
        ("password: sup3rSecretPw", "sup3rSecretPw"),
        (f"to {EMAIL}", EMAIL),
    ],
)
def test_redact_masks_known_shapes(raw: str, must_go: str):
    out = redact(raw)
    assert must_go not in out
    assert "redacted" in out


@pytest.mark.parametrize(
    "benign",
    [
        "a1b2c3d4-e5f6-4789-abcd-0123456789ab",  # a UUID (e.g. a correlation_id) — not a secret
        "processed 42 rows in 3.14s",
        "job JOB-0007 lifecycle=archived status=active",  # 'status=active' is not a secret keyword
        "sheet 8933909738770308 updated",
    ],
)
def test_redact_does_not_over_redact_benign_text(benign: str):
    assert redact(benign) == benign


def test_redact_none_and_empty_and_idempotent():
    assert redact(None) == ""
    assert redact("") == ""
    once = redact(f"boom {AWS_KEY}")
    assert redact(once) == once  # re-redacting a redacted string is a no-op


def test_redact_never_raises_returns_original_on_internal_error(monkeypatch):
    # A redaction bug must NOT break error surfacing — broad-except returns the input unchanged.
    class _Boom:
        def sub(self, *_a, **_k):
            raise RuntimeError("pattern engine exploded")

    monkeypatch.setattr(redact_module, "_EMAIL_PATTERN", _Boom())
    assert redact("plain text") == "plain text"


# ---- migration scripts never print/log a keychain secret (mechanical §54) ---

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "scripts" / "migrations"


def _migration_files() -> list[Path]:
    if not _MIGRATIONS_DIR.is_dir():
        return []
    return sorted(p for p in _MIGRATIONS_DIR.glob("*.py") if p.name != "__init__.py")


@pytest.mark.parametrize(
    "path", _migration_files(), ids=[p.name for p in _migration_files()] or ["<none>"]
)
def test_migration_scripts_never_emit_a_keychain_secret(path: Path) -> None:
    """AST guard: names bound from `keychain.get_secret(...)` in a migration script are never passed
    to `print(...)` / `error_log.log(...)` (directly or inside an f-string) — §54's migration clause.
    Mechanical + currently no live offender; catches a future one before it ships."""
    if not path or not path.exists():  # the <none> placeholder case
        pytest.skip("no migration scripts present")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    secret_names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "get_secret"
        ):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    secret_names.add(tgt.id)
    if not secret_names:
        return  # this migration binds no secret — nothing to guard

    def _names_in(expr: ast.AST) -> set[str]:
        return {n.id for n in ast.walk(expr) if isinstance(n, ast.Name)}

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = None
        if isinstance(node.func, ast.Name):
            fname = node.func.id
        elif isinstance(node.func, ast.Attribute):
            fname = node.func.attr
        if fname not in ("print", "log"):
            continue
        for arg in node.args:
            if secret_names & _names_in(arg):
                offenders.append(f"{path.name}:{node.lineno} passes a keychain secret to {fname}()")
    assert not offenders, "migration script leaks a keychain secret to a log/print: " + "; ".join(
        offenders
    )
