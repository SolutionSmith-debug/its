import { useState, useEffect, useCallback, useRef } from "react";
import type { FormEvent } from "react";
import * as api from "../lib/po";
import { useAuth } from "../lib/auth";
import { PageShell } from "../components/PageShell";

// PO Configuration (Administration) — the browser EDITOR for the three config classes that print on
// every purchase order: the Purchaser identity (D5), the ship-to-state tax table (D8), and the
// terms-library profiles (D6/S3). It reads the current values from the SAME session + cap.po.manage
// routes the builder uses — GET /api/po/config (purchaser + tax) and GET /api/po/terms (the curated
// profile view) — and, for an admin holding cap.po.manage, edits them through the §50 send-free queue.
//
// THE §50 BOUNDARY (made visible in the UI): purchaser/tax/terms live in version-controlled config
// (po_materials/config/*.json) and sha256-PINNED terms files (po_materials/terms/*.md). Editing them
// is a privileged code-actuation (Operational Standards §50) with a legal-review gate on terms text.
// This editor NEVER writes those files directly — it POSTs a change to the cloud queue
// (POST /api/config/requests, send-free), the Mac config daemon is the sole actuator that validates →
// git-commits → auto-deploys the value. So the SPA can only QUEUE; the change goes live (or fails,
// never silently) through the status monitor below. A new terms version ships legal_review: pending
// and is NOT used on a PO until the operator clears it + points current_version at it — the editor
// mints the version; activation is a separate operator step (the deliberate legal gate).
//
// The read affordances are ungated (visibility for the office); ONLY the edit forms are wrapped in
// {canManage} — and the Worker re-gates every write per-workstream (Invariant 2), so the SPA gating
// is convenience, never the boundary.
//
// VISUAL: the same URS-Marine dash look as the Materials Catalog / Vendors admin pages — `.card
// dash-section` blocks, gold-underlined `.jha__section-title` heads, `.dash-chip` chips, the shared
// `.field` edit-form idiom, and the `.form-editor__*` status-monitor stepper reused from PublishMonitor.

const WORKSTREAM = "po_materials";

/** Basis points → a fixed 2-decimal percent string (900 → "9.00%"). Integer-safe display. */
function bpToPct(bp: number): string {
  return `${(bp / 100).toFixed(2)}%`;
}

/** Basis points → a bare percent number string for the editable rate field (900 → "9.00"). */
function bpToPctInput(bp: number): string {
  return (bp / 100).toFixed(2);
}

function FieldInput({
  label,
  value,
  onChange,
  className,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  className?: string;
  placeholder?: string;
}) {
  return (
    <label className={`field${className ? ` ${className}` : ""}`}>
      <span className="field__label">{label}</span>
      <input
        className="field__input"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

function FieldTextarea({
  label,
  value,
  onChange,
  rows = 4,
  maxLength = 8000,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  rows?: number;
  maxLength?: number;
  placeholder?: string;
}) {
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      <textarea
        className="field__textarea"
        aria-label={label}
        value={value}
        rows={rows}
        maxLength={maxLength}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

// ── Editor form buffers (flat all-strings, per the admin edit-form idiom) ──────────────────────────
type PurchaserForm = { entity: string; address_lines: string; phone: string; to: string; cc: string };
type TaxRow = { state: string; name: string; rate: string }; // rate entered as a PERCENT string
type TermsForm = { profile_id: string; target_version: string; text: string };
type MakeCurrentForm = { profile_id: string; target_version: string; confirmed: boolean };

export function PoConfigPage({ onBack }: { onBack: () => void }) {
  const { user } = useAuth();
  const caps = user?.capabilities ?? [];
  const canManage = caps.includes("cap.po.manage"); // UI affordance only — the Worker re-gates every write

  const [config, setConfig] = useState<api.PoConfig | null>(null);
  const [terms, setTerms] = useState<api.TermsProfile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Write feedback (shared across the three editors) + a bump that re-polls the status monitor.
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [refreshSignal, setRefreshSignal] = useState(0);

  // Which editor is open + its buffer.
  const [purchaserOpen, setPurchaserOpen] = useState(false);
  const [pf, setPf] = useState<PurchaserForm>({ entity: "", address_lines: "", phone: "", to: "", cc: "" });
  const [taxOpen, setTaxOpen] = useState(false);
  const [taxRows, setTaxRows] = useState<TaxRow[]>([]);
  const [termsOpen, setTermsOpen] = useState(false);
  const [tf, setTf] = useState<TermsForm>({ profile_id: "", target_version: "", text: "" });
  const [makeCurrentOpen, setMakeCurrentOpen] = useState(false);
  const [mcf, setMcf] = useState<MakeCurrentForm>({ profile_id: "", target_version: "", confirmed: false });
  const [mcVersions, setMcVersions] = useState<api.TermsVersionRow[]>([]);
  const [mcCurrent, setMcCurrent] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [cfg, tp] = await Promise.all([api.fetchPoConfig(), api.fetchTerms()]);
      setConfig(cfg);
      setTerms(tp);
    } catch {
      setError("Could not load PO configuration. Check your connection and try again.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Sorted tax rows — union of the rate table and the state-name map, so a state with a name but
  // no explicit rate (or vice-versa) still shows, never silently dropped.
  const taxStates = config
    ? Array.from(
        new Set([...Object.keys(config.tax.rates_bp), ...Object.keys(config.tax.state_names)]),
      ).sort()
    : [];

  // ── Editor open (seed the buffer from the current value) ─────────────────────────────────────────
  function openPurchaser() {
    if (!config) return;
    const p = config.purchaser;
    setPf({
      entity: p.entity,
      address_lines: p.address_lines.join("\n"),
      phone: p.phone,
      to: p.invoice_routing.to,
      cc: p.invoice_routing.cc.join("\n"),
    });
    setMsg(null);
    setPurchaserOpen(true);
  }

  function openTax() {
    if (!config) return;
    setTaxRows(
      taxStates.map((st) => ({
        state: st,
        name: config.tax.state_names[st] ?? "",
        rate: config.tax.rates_bp[st] == null ? "" : bpToPctInput(config.tax.rates_bp[st]),
      })),
    );
    setMsg(null);
    setTaxOpen(true);
  }

  // Only library profiles have versioned, editable text (attach profiles render a fixed line).
  const libraryTerms = terms.filter((t) => t.kind === "library");

  // Pre-fill the textarea with a profile's CURRENT version text so the operator edits from the live
  // wording rather than a blank box (they then save it as a NEW version via add_version). A failed
  // fetch / no-editable-text profile leaves the box empty — the operator can still type from scratch.
  async function loadTermsText(profileId: string) {
    try {
      const { text } = await api.fetchTermsText(profileId);
      setTf((cur) => ({ ...cur, profile_id: profileId, text }));
    } catch {
      setTf((cur) => ({ ...cur, profile_id: profileId, text: "" }));
    }
  }

  function openTerms() {
    const first = libraryTerms[0]?.id ?? "";
    setTf({ profile_id: first, target_version: "", text: "" });
    setMsg(null);
    setTermsOpen(true);
    if (first) void loadTermsText(first);
  }

  // ── Editor submit (the 5-step envelope: guard → busy → await → reload+bump → catch → finally) ─────
  async function submitPurchaser(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const entity = pf.entity.trim();
    const to = pf.to.trim();
    if (!entity) {
      setMsg({ ok: false, text: "The purchaser entity is required." });
      return;
    }
    if (!to) {
      setMsg({ ok: false, text: "The invoice-routing To address is required." });
      return;
    }
    const payload = {
      entity,
      address_lines: pf.address_lines.split("\n").map((s) => s.trim()).filter(Boolean),
      phone: pf.phone.trim(),
      invoice_routing: {
        to,
        cc: pf.cc.split("\n").map((s) => s.trim()).filter(Boolean),
      },
    };
    setBusy(true);
    setMsg(null);
    try {
      await api.submitConfigEdit({ workstream: WORKSTREAM, artifact_key: "purchaser", op: "edit", payload });
      setPurchaserOpen(false);
      setMsg({ ok: true, text: "Queued — the purchaser change will go live after review. Track it below." });
      setRefreshSignal((n) => n + 1);
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Submit failed." });
    } finally {
      setBusy(false);
    }
  }

  async function submitTax(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const rates_bp: Record<string, number> = {};
    const state_names: Record<string, string> = {};
    for (const row of taxRows) {
      const st = row.state.trim().toUpperCase();
      if (!/^[A-Z]{2}$/.test(st)) {
        setMsg({ ok: false, text: `Each state must be a 2-letter code (got "${row.state}").` });
        return;
      }
      if (rates_bp[st] !== undefined) {
        setMsg({ ok: false, text: `${st} is listed twice — remove the duplicate.` });
        return;
      }
      const bp = api.pctToBp(row.rate);
      if (bp === null) {
        setMsg({ ok: false, text: `Enter the ${st} rate as a percent 0–100 with at most 2 decimals (e.g. 9.25).` });
        return;
      }
      rates_bp[st] = bp;
      if (row.name.trim()) state_names[st] = row.name.trim();
    }
    if (Object.keys(rates_bp).length === 0) {
      setMsg({ ok: false, text: "Add at least one state rate." });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await api.submitConfigEdit({
        workstream: WORKSTREAM,
        artifact_key: "tax",
        op: "edit",
        payload: { rates_bp, state_names },
      });
      setTaxOpen(false);
      setMsg({ ok: true, text: "Queued — the tax-table change will go live after review. Track it below." });
      setRefreshSignal((n) => n + 1);
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Submit failed." });
    } finally {
      setBusy(false);
    }
  }

  async function submitTerms(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const profile_id = tf.profile_id.trim();
    const targetVersion = tf.target_version.trim();
    const text = tf.text.trim();
    if (!profile_id) {
      setMsg({ ok: false, text: "Pick a terms profile." });
      return;
    }
    if (!/^[a-z0-9_]+$/.test(targetVersion) || targetVersion.length > 64) {
      setMsg({ ok: false, text: "The version name must be lowercase letters, numbers, and underscores (e.g. standard_17_v2)." });
      return;
    }
    if (!text) {
      setMsg({ ok: false, text: "Enter the clause text for this version." });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await api.submitConfigEdit({
        workstream: WORKSTREAM,
        artifact_key: "terms",
        op: "add_version",
        payload: { profile_id, text },
        target_version: targetVersion,
      });
      setTermsOpen(false);
      setMsg({
        ok: true,
        text: "Queued — the new terms version will be minted with legal_review: pending. Track it below.",
      });
      setRefreshSignal((n) => n + 1);
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Submit failed." });
    } finally {
      setBusy(false);
    }
  }

  // ── Make-current (the legal-activation op: clears legal_review + repoints current_version) ─────
  async function loadVersions(profileId: string) {
    try {
      const v = await api.fetchTermsVersions(profileId);
      setMcVersions(v.versions);
      setMcCurrent(v.current_version);
      // Default the pick to the first NON-current version (the one you'd typically activate).
      const firstOther = v.versions.find((x) => x.version !== v.current_version)?.version;
      setMcf({ profile_id: profileId, target_version: firstOther ?? v.current_version ?? "", confirmed: false });
    } catch {
      setMcVersions([]);
      setMcCurrent(null);
      setMcf({ profile_id: profileId, target_version: "", confirmed: false });
    }
  }

  function openMakeCurrent() {
    const first = libraryTerms[0]?.id ?? "";
    setMcf({ profile_id: first, target_version: "", confirmed: false });
    setMcVersions([]);
    setMcCurrent(null);
    setMsg(null);
    setMakeCurrentOpen(true);
    if (first) void loadVersions(first);
  }

  async function submitMakeCurrent(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!mcf.profile_id) {
      setMsg({ ok: false, text: "Pick a terms profile." });
      return;
    }
    if (!mcf.target_version) {
      setMsg({ ok: false, text: "Pick a version to make current." });
      return;
    }
    if (!mcf.confirmed) {
      setMsg({ ok: false, text: "Confirm you have reviewed this version's legal text before making it live." });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await api.submitConfigEdit({
        workstream: WORKSTREAM,
        artifact_key: "terms",
        op: "set_current",
        payload: { profile_id: mcf.profile_id },
        target_version: mcf.target_version,
      });
      setMakeCurrentOpen(false);
      setMsg({
        ok: true,
        text: "Queued — the version will be cleared and made current after review + deploy. Track it below.",
      });
      setRefreshSignal((n) => n + 1);
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Submit failed." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageShell onHome={onBack}>
      <h2 className="page__heading">PO Configuration</h2>
      <p className="muted po-config__intro">
        The identity, tax, and terms values that print on every purchase order. An admin can edit
        them here — each change is queued for review and takes effect (or fails, never silently) once
        the operator&rsquo;s config actuator validates and deploys it. Editing terms text mints a new
        version behind a legal-review gate; it is not used on a PO until the operator clears it.
      </p>

      {msg && <div className={`banner ${msg.ok ? "banner--ok" : "banner--err"}`}>{msg.text}</div>}
      {error && <div className="banner banner--err">{error}</div>}
      {loading && !config && <div className="centered muted">Loading…</div>}

      {config && (
        <>
          {/* ── Purchaser identity (D5) ─────────────────────────────────────────────── */}
          <section className="card dash-section" aria-label="Purchaser identity">
            <h3 className="jha__section-title">Purchaser</h3>
            <div className="po-config__block">
              <div className="po-config__entity">{config.purchaser.entity}</div>
              {config.purchaser.address_lines.map((line, i) => (
                <div key={i} className="po-config__line muted">
                  {line}
                </div>
              ))}
              {config.purchaser.phone && (
                <div className="po-config__line muted">{config.purchaser.phone}</div>
              )}
            </div>
            <div className="po-config__block">
              <div className="field__label">Invoice routing</div>
              <div className="dash-chips">
                <span className="dash-chip">To: {config.purchaser.invoice_routing.to}</span>
                {config.purchaser.invoice_routing.cc.map((cc) => (
                  <span key={cc} className="dash-chip">
                    CC: {cc}
                  </span>
                ))}
              </div>
            </div>

            {canManage &&
              (purchaserOpen ? (
                <form className="accounts__editor" onSubmit={submitPurchaser}>
                  <FieldInput label="Entity (required)" value={pf.entity} onChange={(v) => setPf({ ...pf, entity: v })} />
                  <FieldTextarea
                    label="Address lines (one per line)"
                    value={pf.address_lines}
                    rows={3}
                    maxLength={1000}
                    onChange={(v) => setPf({ ...pf, address_lines: v })}
                  />
                  <FieldInput label="Phone" value={pf.phone} onChange={(v) => setPf({ ...pf, phone: v })} />
                  <FieldInput label="Invoice routing — To (required)" value={pf.to} onChange={(v) => setPf({ ...pf, to: v })} />
                  <FieldTextarea
                    label="Invoice routing — CC (one email per line)"
                    value={pf.cc}
                    rows={3}
                    maxLength={1000}
                    onChange={(v) => setPf({ ...pf, cc: v })}
                  />
                  <div className="jha__actions">
                    <button className="btn btn--primary" type="submit">
                      {busy ? "Working…" : "Queue change"}
                    </button>
                    <button className="btn btn--secondary" type="button" onClick={() => setPurchaserOpen(false)}>
                      Cancel
                    </button>
                  </div>
                </form>
              ) : (
                <div className="jha__actions">
                  <button className="btn btn--edit" type="button" onClick={openPurchaser}>
                    Edit purchaser
                  </button>
                </div>
              ))}
          </section>

          {/* ── Ship-to-state tax table (D8) ────────────────────────────────────────── */}
          <section className="card dash-section" aria-label="Tax table">
            <h3 className="jha__section-title">
              Sales tax by ship-to state <span className="dash-pill">{taxStates.length}</span>
            </h3>
            {taxStates.length === 0 ? (
              <p className="muted">No tax states configured.</p>
            ) : (
              <div className="dash-grid">
                {taxStates.map((st) => {
                  const bp = config.tax.rates_bp[st];
                  return (
                    <section key={st} className="card po-config__tax-card">
                      <div className="po-config__tax-rate">{bp == null ? "—" : bpToPct(bp)}</div>
                      <div className="dash-chips">
                        <span className="dash-chip">{st}</span>
                        {config.tax.state_names[st] && (
                          <span className="dash-chip">{config.tax.state_names[st]}</span>
                        )}
                      </div>
                    </section>
                  );
                })}
              </div>
            )}

            {canManage &&
              (taxOpen ? (
                <form className="accounts__editor" onSubmit={submitTax}>
                  <p className="muted">Enter each rate as a percent (e.g. 9.25) — it is stored as integer basis points.</p>
                  {taxRows.map((row, i) => (
                    <div className="po-config__tax-edit-row" key={i}>
                      <FieldInput
                        label="State"
                        className="field--state"
                        value={row.state}
                        placeholder="IL"
                        onChange={(v) => setTaxRows(taxRows.map((r, j) => (j === i ? { ...r, state: v } : r)))}
                      />
                      <FieldInput
                        label="Name"
                        value={row.name}
                        placeholder="Illinois"
                        onChange={(v) => setTaxRows(taxRows.map((r, j) => (j === i ? { ...r, name: v } : r)))}
                      />
                      <FieldInput
                        label="Rate %"
                        className="field--rate"
                        value={row.rate}
                        placeholder="9.25"
                        onChange={(v) => setTaxRows(taxRows.map((r, j) => (j === i ? { ...r, rate: v } : r)))}
                      />
                      <button
                        className="btn btn--retire"
                        type="button"
                        aria-label={`Remove ${row.state || "row"}`}
                        onClick={() => setTaxRows(taxRows.filter((_, j) => j !== i))}
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                  <div className="jha__actions">
                    <button
                      className="btn btn--secondary"
                      type="button"
                      onClick={() => setTaxRows([...taxRows, { state: "", name: "", rate: "" }])}
                    >
                      + Add state
                    </button>
                  </div>
                  <div className="jha__actions">
                    <button className="btn btn--primary" type="submit">
                      {busy ? "Working…" : "Queue change"}
                    </button>
                    <button className="btn btn--secondary" type="button" onClick={() => setTaxOpen(false)}>
                      Cancel
                    </button>
                  </div>
                </form>
              ) : (
                <div className="jha__actions">
                  <button className="btn btn--edit" type="button" onClick={openTax}>
                    Edit tax table
                  </button>
                </div>
              ))}
          </section>

          {/* ── Terms-library profiles (D6/S3) ──────────────────────────────────────── */}
          <section className="card dash-section" aria-label="Terms profiles">
            <h3 className="jha__section-title">
              Terms &amp; conditions profiles <span className="dash-pill">{terms.length}</span>
            </h3>
            {terms.length === 0 ? (
              <p className="muted">No terms profiles configured.</p>
            ) : (
              <div className="dash-grid">
                {terms.map((t) => (
                  <section key={t.id} className="card" aria-label={`${t.label} terms profile`}>
                    <div className="po-config__terms-head">
                      <strong>{t.label}</strong>
                    </div>
                    <div className="dash-chips">
                      <span className="dash-chip">{t.kind === "attach" ? "Attached" : "Library"}</span>
                      {t.current_version && <span className="dash-chip">v: {t.current_version}</span>}
                    </div>
                    {t.description && <p className="muted po-config__terms-desc">{t.description}</p>}
                    {t.kind === "attach" && t.render_line ? (
                      <p className="muted po-config__line">{t.render_line}</p>
                    ) : t.tokens.length > 0 ? (
                      <div className="po-config__block">
                        <div className="field__label">Substituted tokens</div>
                        <div className="dash-chips">
                          {t.tokens.map((tok) => (
                            <span key={tok} className="dash-chip">
                              {tok}
                            </span>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </section>
                ))}
              </div>
            )}

            {canManage &&
              (termsOpen ? (
                <form className="accounts__editor" onSubmit={submitTerms}>
                  <p className="po-config__legal-note" role="note">
                    <strong>Legal-review gate.</strong> This mints a NEW terms version with{" "}
                    <code>legal_review: pending</code>. It is NOT used on any PO until the operator
                    clears the legal review and points the profile&rsquo;s <code>current_version</code>{" "}
                    at it — the editor mints the version; activation is a separate operator step.
                  </p>
                  <label className="field">
                    <span className="field__label">Profile</span>
                    <select
                      className="field__input"
                      aria-label="Profile"
                      value={tf.profile_id}
                      onChange={(e) => void loadTermsText(e.target.value)}
                    >
                      {libraryTerms.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <FieldInput
                    label="New version name (lowercase, e.g. standard_17_v2)"
                    value={tf.target_version}
                    placeholder="standard_17_v2"
                    onChange={(v) => setTf({ ...tf, target_version: v })}
                  />
                  <FieldTextarea
                    label="Terms clause text"
                    value={tf.text}
                    rows={8}
                    maxLength={8000}
                    onChange={(v) => setTf({ ...tf, text: v })}
                  />
                  <div className="jha__actions">
                    <button className="btn btn--primary" type="submit">
                      {busy ? "Working…" : "Queue new version"}
                    </button>
                    <button className="btn btn--secondary" type="button" onClick={() => setTermsOpen(false)}>
                      Cancel
                    </button>
                  </div>
                </form>
              ) : makeCurrentOpen ? (
                <form className="accounts__editor" onSubmit={submitMakeCurrent}>
                  <p className="po-config__legal-note" role="note">
                    <strong>Legal activation.</strong> Making a version current CLEARS its legal review
                    and points the profile&rsquo;s <code>current_version</code> at it, so new POs render
                    it. This is the legally-significant step — confirm you have reviewed the exact clause
                    text before making it live.
                  </p>
                  <label className="field">
                    <span className="field__label">Profile</span>
                    <select
                      className="field__input"
                      aria-label="Make-current profile"
                      value={mcf.profile_id}
                      onChange={(e) => void loadVersions(e.target.value)}
                    >
                      {libraryTerms.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field">
                    <span className="field__label">Version to make current</span>
                    <select
                      className="field__input"
                      aria-label="Version to make current"
                      value={mcf.target_version}
                      onChange={(e) => setMcf({ ...mcf, target_version: e.target.value })}
                    >
                      {mcVersions.map((v) => (
                        <option key={v.version} value={v.version}>
                          {v.version} — {v.legal_review}
                          {v.version === mcCurrent ? " (current)" : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field">
                    <span className="field__label">
                      <input
                        type="checkbox"
                        aria-label="I have reviewed this version's legal text"
                        checked={mcf.confirmed}
                        onChange={(e) => setMcf({ ...mcf, confirmed: e.target.checked })}
                      />{" "}
                      I have reviewed this version&rsquo;s legal text — make it live.
                    </span>
                  </label>
                  <div className="jha__actions">
                    <button className="btn btn--primary" type="submit" disabled={busy || !mcf.confirmed}>
                      {busy ? "Working…" : "Make it live"}
                    </button>
                    <button className="btn btn--secondary" type="button" onClick={() => setMakeCurrentOpen(false)}>
                      Cancel
                    </button>
                  </div>
                </form>
              ) : (
                <div className="jha__actions">
                  <button className="btn btn--edit" type="button" onClick={openTerms}>
                    Add a terms version
                  </button>
                  <button className="btn btn--edit" type="button" onClick={openMakeCurrent}>
                    Make a version current
                  </button>
                </div>
              ))}
          </section>

          {/* ── Subcontracts (provisioned placeholder — the SAME editor serves it later) ─────── */}
          <section className="card dash-section po-config__placeholder" aria-label="Subcontracts (coming soon)">
            <h3 className="jha__section-title">Subcontracts</h3>
            <p className="muted">
              Coming with the subcontract workflow — this editor already supports it; its artifacts
              appear here once that workflow is built. No new page is needed.
            </p>
            <div className="jha__actions">
              <button className="btn btn--secondary" type="button" disabled aria-disabled="true">
                Edit subcontracts (coming soon)
              </button>
            </div>
          </section>

          {/* ── Status monitor — each queued change advances queued→validated→tested→live ─── */}
          {canManage && <ConfigStatusMonitor refreshSignal={refreshSignal} />}
        </>
      )}
    </PageShell>
  );
}

// ── Status monitor (a read-only poll of the §50 config queue — the SPA never advances it) ──────────
// Mirrors PublishMonitor: fast (4s) while anything is in flight, slow (20s) once terminal; re-polls
// immediately on `refreshSignal`. A `failed` row is NEVER silent — it shows the RED stage + the
// server's failure_reason verbatim.

const CONFIG_STEPS = [
  { key: "queued", label: "Queued" },
  { key: "validated", label: "Validated" },
  { key: "tested", label: "Tested" },
  { key: "live", label: "Live" },
  { key: "archived", label: "Archived" },
] as const;

const STATUS_INDEX: Record<api.ConfigStatus, number> = {
  queued: 0,
  validated: 1,
  tested: 2,
  merged: 3, // transient toward live → render the Live step "in progress"
  live: 3,
  archived: 4,
  failed: -1,
};

const CONFIG_TERMINAL = new Set<api.ConfigStatus>(["archived", "failed"]);

const CONFIG_OP_LABEL: Record<api.ConfigOp, string> = {
  edit: "Edit",
  add_version: "Add version",
  set_current: "Make current",
};

function fmtTime(t: number): string {
  // config_requests.created_at/updated_at are unix SECONDS (migration 0045 unixepoch()); Date()
  // expects milliseconds, so ×1000.
  const d = new Date(t * 1000);
  if (Number.isNaN(d.getTime())) return String(t);
  return d.toLocaleString();
}

// Map the recorded failed_stage onto a stepper index so the RED dot lands sensibly.
function stepIndexForFailure(req: api.ConfigRequest): number {
  const stage = (req.failed_stage ?? "").toLowerCase();
  if (stage.includes("archive")) return 4;
  if (stage.includes("live") || stage.includes("merge") || stage.includes("deploy")) return 3;
  if (stage.includes("test")) return 2;
  if (stage.includes("valid")) return 1;
  if (stage.includes("queue")) return 0;
  return 1;
}

export function ConfigStatusMonitor({ refreshSignal }: { refreshSignal?: number }) {
  const [requests, setRequests] = useState<api.ConfigRequest[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      const rows = await api.fetchConfigStatus();
      setRequests(rows);
      setErr(null);
      return rows;
    } catch {
      setErr("Could not load the config-change status.");
      return null;
    }
  }, []);

  useEffect(() => {
    let active = true;
    const tick = async () => {
      if (!active) return;
      const rows = await load();
      if (!active) return;
      const inFlight = (rows ?? []).some((r) => !CONFIG_TERMINAL.has(r.status));
      timer.current = setTimeout(() => void tick(), inFlight ? 4000 : 20000);
    };
    void tick();
    return () => {
      active = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [load]);

  useEffect(() => {
    if (refreshSignal !== undefined) void load();
  }, [refreshSignal, load]);

  return (
    <section className="card form-editor__monitor" aria-label="Config change status">
      <div className="form-editor__monitor-head">
        <h2 className="page__heading">Config change status</h2>
        <div className="jha__actions" style={{ marginTop: 0 }}>
          <button type="button" className="btn btn--secondary" onClick={() => void load()}>
            Refresh
          </button>
        </div>
      </div>
      {err ? (
        <p className="login__error" role="alert">
          {err}
        </p>
      ) : requests === null ? (
        <p className="muted">Loading…</p>
      ) : requests.length === 0 ? (
        <p className="muted">No config changes yet.</p>
      ) : (
        <ul className="form-editor__monitor-list">
          {requests.map((r) => (
            <ConfigRequestRow key={r.id} req={r} onCleared={() => void load()} />
          ))}
        </ul>
      )}
    </section>
  );
}

// The resting states a request may be cleared (soft-dismissed) from — LOCKSTEP with the Worker's
// CONFIG_CLEARABLE_STATUSES (worker/config.ts). 'live' counts (the deploy succeeded); the in-flight
// states never do (the Worker refuses them 409).
const CONFIG_CLEARABLE = new Set<api.ConfigStatus>(["live", "archived", "failed"]);

function ConfigRequestRow({ req, onCleared }: { req: api.ConfigRequest; onCleared: () => void }) {
  const failed = req.status === "failed";
  const reached = STATUS_INDEX[req.status];
  const clearable = CONFIG_CLEARABLE.has(req.status);
  const [clearing, setClearing] = useState(false);
  const [clearErr, setClearErr] = useState<string | null>(null);

  async function clear() {
    if (clearing) return;
    setClearing(true);
    setClearErr(null);
    try {
      await api.clearConfigRequest(req.id);
      onCleared(); // re-poll: the cleared row drops out of the default monitor view
    } catch (e) {
      setClearErr(e instanceof Error ? e.message : "Could not clear this change.");
      setClearing(false);
    }
  }

  return (
    <li className={`form-editor__req${failed ? " form-editor__req--failed" : ""}`}>
      <div className="form-editor__req-head">
        <span className="form-editor__req-op">{CONFIG_OP_LABEL[req.op] ?? req.op}</span>
        <span className="form-editor__req-target">
          {req.workstream}/{req.artifact_key}
        </span>
        <span className={`form-editor__req-status form-editor__req-status--${req.status}`}>{req.status}</span>
        <span className="form-editor__req-time muted">{fmtTime(req.updated_at)}</span>
        {clearable && (
          <button
            type="button"
            className="btn btn--secondary form-editor__req-clear"
            onClick={() => void clear()}
            disabled={clearing}
            aria-label={`Clear ${CONFIG_OP_LABEL[req.op] ?? req.op} ${req.workstream}/${req.artifact_key}`}
          >
            {clearing ? "Clearing…" : "Clear"}
          </button>
        )}
      </div>
      <ol className="form-editor__stepper" aria-label="Config change progress">
        {CONFIG_STEPS.map((step, i) => {
          let state: "done" | "current" | "todo" | "failed";
          if (failed) {
            const at = stepIndexForFailure(req);
            state = i < at ? "done" : i === at ? "failed" : "todo";
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
      {clearErr ? (
        <p className="form-editor__req-failure" role="alert">
          {clearErr}
        </p>
      ) : null}
    </li>
  );
}
