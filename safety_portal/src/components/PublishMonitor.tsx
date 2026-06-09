import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../lib/api";

/**
 * Publish status monitor (Phase-2 slice 3, the editor's send-free feedback loop). Polls
 * GET /api/admin/publish-status and renders each recent request as a stepper
 * (Queued → Validated → Tested → Live → Archived). A `failed` request shows RED at the
 * stage it failed, with the server's failure_reason verbatim. The SPA NEVER advances the
 * machine — the Mac daemon does; this is a read-only window onto it. Polling stops when
 * the tab unmounts; it slows once nothing is in flight (terminal-only) to avoid a busy
 * loop, and the operator can refresh on demand.
 */

// Display order of the happy-path stages. `merged` is folded into the Live step (it is a
// transient internal stage between tested and live); `archived` is the terminal success.
const STEPS = [
  { key: "queued", label: "Queued" },
  { key: "validated", label: "Validated" },
  { key: "tested", label: "Tested" },
  { key: "live", label: "Live" },
  { key: "archived", label: "Archived" },
] as const;

// Map each status onto a 0-based index into STEPS (how far the happy path has advanced).
const STATUS_INDEX: Record<api.PublishRequest["status"], number> = {
  queued: 0,
  validated: 1,
  tested: 2,
  merged: 3, // mid-flight toward live → render as the Live step "in progress"
  live: 3,
  archived: 4,
  failed: -1,
};

const TERMINAL = new Set<api.PublishRequest["status"]>(["archived", "failed"]);

const OP_LABEL: Record<api.PublishOp, string> = {
  create: "Create",
  edit: "Edit",
  add_version: "Add version",
  delete: "Retire",
  rollback: "Rollback",
};

function fmtTime(t: string | number): string {
  const d = typeof t === "number" ? new Date(t) : new Date(t);
  if (Number.isNaN(d.getTime())) return String(t);
  return d.toLocaleString();
}

export function PublishMonitor({ refreshSignal }: { refreshSignal?: number }) {
  const [requests, setRequests] = useState<api.PublishRequest[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      const rows = await api.fetchPublishStatus();
      setRequests(rows);
      setErr(null);
      return rows;
    } catch (e) {
      setErr(e instanceof api.AdminError ? "Not authorized to view publish status." : "Could not load publish status.");
      return null;
    }
  }, []);

  // Poll: fast (4s) while anything is in flight, slow (20s) once everything is terminal.
  useEffect(() => {
    let active = true;
    const tick = async () => {
      if (!active) return;
      const rows = await load();
      if (!active) return;
      const inFlight = (rows ?? []).some((r) => !TERMINAL.has(r.status));
      timer.current = setTimeout(() => void tick(), inFlight ? 4000 : 20000);
    };
    void tick();
    return () => {
      active = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [load]);

  // Re-poll immediately after a publish is enqueued (parent bumps refreshSignal).
  useEffect(() => {
    if (refreshSignal !== undefined) void load();
  }, [refreshSignal, load]);

  // "Clear finished" — remove terminal (archived/failed) rows from the monitor. Only
  // offered when there's something finished to clear; in-flight rows are never touched.
  const hasFinished = (requests ?? []).some((r) => TERMINAL.has(r.status));
  const onClear = useCallback(async () => {
    setClearing(true);
    try {
      await api.dismissFinishedPublishes();
      await load();
    } catch {
      setErr("Could not clear finished publishes.");
    } finally {
      setClearing(false);
    }
  }, [load]);

  return (
    <section className="card form-editor__monitor" aria-label="Publish status">
      <div className="form-editor__monitor-head">
        <h2 className="page__heading">Publish status</h2>
        <div className="jha__actions" style={{ marginTop: 0 }}>
          {hasFinished ? (
            <button type="button" className="btn btn--secondary" disabled={clearing} onClick={() => void onClear()}>
              {clearing ? "Clearing…" : "Clear finished"}
            </button>
          ) : null}
          <button type="button" className="btn btn--secondary" onClick={() => void load()}>
            Refresh
          </button>
        </div>
      </div>
      {err ? (
        <p className="login__error" role="alert">{err}</p>
      ) : requests === null ? (
        <p className="muted">Loading…</p>
      ) : requests.length === 0 ? (
        <p className="muted">No publish requests yet.</p>
      ) : (
        <ul className="form-editor__monitor-list">
          {requests.map((r) => (
            <RequestRow key={r.id} req={r} />
          ))}
        </ul>
      )}
    </section>
  );
}

function RequestRow({ req }: { req: api.PublishRequest }) {
  const failed = req.status === "failed";
  const reached = STATUS_INDEX[req.status];
  const target = req.target_form_code ?? req.identity;
  return (
    <li className={`form-editor__req${failed ? " form-editor__req--failed" : ""}`}>
      <div className="form-editor__req-head">
        <span className="form-editor__req-op">{OP_LABEL[req.op] ?? req.op}</span>
        <span className="form-editor__req-target">{target}</span>
        <span className={`form-editor__req-status form-editor__req-status--${req.status}`}>
          {req.status}
        </span>
        <span className="form-editor__req-time muted">{fmtTime(req.updated_at)}</span>
      </div>
      {/* delete/rollback don't traverse the create stepper meaningfully, but the same
          status machine still applies — render the stepper for all ops. */}
      <ol className="form-editor__stepper" aria-label="Publish progress">
        {STEPS.map((step, i) => {
          let state: "done" | "current" | "todo" | "failed";
          if (failed) {
            // RED the stage it died at (reached index falls back to the recorded stage);
            // earlier steps are done, later steps are unreached.
            state = i < stepIndexForFailure(req) ? "done" : i === stepIndexForFailure(req) ? "failed" : "todo";
          } else if (i < reached) {
            state = "done";
          } else if (i === reached) {
            state = req.status === "archived" ? "done" : "current";
          } else {
            state = "todo";
          }
          return (
            <li key={step.key} className={`form-editor__step form-editor__step--${state}`}>
              <span className="form-editor__step-dot" aria-hidden="true" />
              <span className="form-editor__step-label">{step.label}</span>
            </li>
          );
        })}
      </ol>
      {failed ? (
        <p className="form-editor__req-failure" role="alert">
          Failed{req.failed_stage ? ` at ${req.failed_stage}` : ""}
          {req.failure_reason ? `: ${req.failure_reason}` : "."}
        </p>
      ) : null}
    </li>
  );
}

// On failure the server records failed_stage (a free string). Map the common stage names
// onto a stepper index so the RED dot lands sensibly; default to the Validated step (the
// first gate) when we can't map it.
function stepIndexForFailure(req: api.PublishRequest): number {
  const stage = (req.failed_stage ?? "").toLowerCase();
  if (stage.includes("archive")) return 4;
  if (stage.includes("live") || stage.includes("merge") || stage.includes("deploy")) return 3;
  if (stage.includes("test")) return 2;
  if (stage.includes("valid")) return 1;
  if (stage.includes("queue")) return 0;
  return 1;
}
