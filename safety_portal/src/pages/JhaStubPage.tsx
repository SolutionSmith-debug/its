import { useState } from "react";
import { AppHeader } from "../components/AppHeader";
import { SignaturePad } from "../components/SignaturePad";

interface TaskRow {
  task: string;
  hazards: string;
  mitigation: string;
}

const emptyRow: TaskRow = { task: "", hazards: "", mitigation: "" };

/** Today as YYYY-MM-DD (local). The PM may freely backdate the work date (Q4). */
function todayIso(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * Hard-coded JHA stub (Phase 2). Mirrors the real Daily Job Hazard Analysis layout
 * so it's representative, not a placeholder. In-memory only — no submission, no PDF,
 * no Smartsheet, no email (all later phases). Captures ONE signature as SVG path
 * data to prove the signature mechanism (multi-row capture is Phase 4).
 *
 * Q4: the PM sets a work date; NO submission timestamp is surfaced anywhere.
 */
export function JhaStubPage({ onBack }: { onBack: () => void }) {
  const [workDate, setWorkDate] = useState(todayIso());
  const [location, setLocation] = useState("");
  const [job, setJob] = useState("");
  const [crew, setCrew] = useState("");
  const [rows, setRows] = useState<TaskRow[]>([{ ...emptyRow }, { ...emptyRow }, { ...emptyRow }]);
  const [ackName, setAckName] = useState("");
  const [ackCompany, setAckCompany] = useState("");
  const [signature, setSignature] = useState(""); // SVG path data

  function updateRow(i: number, key: keyof TaskRow, value: string) {
    setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, [key]: value } : r)));
  }
  function addRow() {
    setRows((rs) => [...rs, { ...emptyRow }]);
  }
  function removeRow(i: number) {
    setRows((rs) => (rs.length > 1 ? rs.filter((_, idx) => idx !== i) : rs));
  }

  return (
    <div className="page">
      <AppHeader
        title="Job Hazard Analysis"
        action={
          <button className="btn btn--ghost" onClick={onBack}>
            Back
          </button>
        }
      />
      <main className="page__main">
        <p className="jha__notice">
          <strong>Phase 2 preview.</strong> This form renders and captures input locally — it does
          not submit, generate a PDF, or write to any system yet. Submission lands in a later phase.
        </p>

        {/* ── Header fields ──────────────────────────────────────────── */}
        <section className="jha__section">
          <div className="jha__grid-2">
            <label className="field">
              <span className="field__label">Date (work date)</span>
              <input
                className="field__input"
                type="date"
                value={workDate}
                onChange={(e) => setWorkDate(e.target.value)}
              />
            </label>
            <label className="field">
              <span className="field__label">Work Location</span>
              <input
                className="field__input"
                type="text"
                value={location}
                onChange={(e) => setLocation(e.target.value)}
              />
            </label>
            <label className="field">
              <span className="field__label">Job Being Performed</span>
              <input
                className="field__input"
                type="text"
                value={job}
                onChange={(e) => setJob(e.target.value)}
              />
            </label>
            <label className="field">
              <span className="field__label">Crew Members</span>
              <input
                className="field__input"
                type="text"
                value={crew}
                onChange={(e) => setCrew(e.target.value)}
              />
            </label>
          </div>
        </section>

        {/* ── Tasks / Major Hazards / Mitigation ─────────────────────── */}
        <section className="jha__section">
          <h2 className="jha__section-title">Tasks · Major Hazards · Mitigation</h2>
          <div className="jha__rows">
            {rows.map((row, i) => (
              <div className="jha__row" key={i}>
                {rows.length > 1 ? (
                  <button
                    type="button"
                    className="jha__row-remove"
                    aria-label={`Remove row ${i + 1}`}
                    onClick={() => removeRow(i)}
                  >
                    ✕
                  </button>
                ) : null}
                <label className="field" style={{ margin: 0 }}>
                  <span className="jha__row-head">Task</span>
                  <textarea
                    className="field__textarea"
                    value={row.task}
                    onChange={(e) => updateRow(i, "task", e.target.value)}
                  />
                </label>
                <label className="field" style={{ margin: 0 }}>
                  <span className="jha__row-head">Major Hazards</span>
                  <textarea
                    className="field__textarea"
                    value={row.hazards}
                    onChange={(e) => updateRow(i, "hazards", e.target.value)}
                  />
                </label>
                <label className="field" style={{ margin: 0 }}>
                  <span className="jha__row-head">Mitigation</span>
                  <textarea
                    className="field__textarea"
                    value={row.mitigation}
                    onChange={(e) => updateRow(i, "mitigation", e.target.value)}
                  />
                </label>
              </div>
            ))}
          </div>
          <div className="jha__actions">
            <button type="button" className="btn btn--secondary" onClick={addRow}>
              + Add row
            </button>
          </div>
          <p className="jha__footer-line">IF CONDITIONS CHANGE…REVIEW AND REVISE THE PLAN.</p>
        </section>

        {/* ── Worker Acknowledgement (single signature for the stub) ─── */}
        <section className="jha__section">
          <h2 className="jha__section-title">Worker Acknowledgement</h2>
          <div className="jha__grid-2">
            <label className="field">
              <span className="field__label">Worker Name</span>
              <input
                className="field__input"
                type="text"
                value={ackName}
                onChange={(e) => setAckName(e.target.value)}
              />
            </label>
            <label className="field">
              <span className="field__label">Company</span>
              <input
                className="field__input"
                type="text"
                value={ackCompany}
                onChange={(e) => setAckCompany(e.target.value)}
              />
            </label>
          </div>
          <span className="field__label">Signature</span>
          <SignaturePad onChange={(svgPath, isEmpty) => setSignature(isEmpty ? "" : svgPath)} />
          <p className="sig__hint" aria-live="polite" style={{ marginTop: 6 }}>
            {signature
              ? `Signature captured — ${signature.length} chars of SVG path data.`
              : "No signature captured yet."}
          </p>
        </section>
      </main>
    </div>
  );
}
