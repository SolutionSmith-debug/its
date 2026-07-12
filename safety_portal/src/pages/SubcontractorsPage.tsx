import { useState, useEffect, useCallback } from "react";
import type { FormEvent } from "react";
import * as api from "../lib/subcontracts";
import { TRADES, US_STATES, stateName } from "../lib/subcontracts";
import { useAuth } from "../lib/auth";
import { PageShell } from "../components/PageShell";

// SC-S5 — subcontractor directory. Faithful mirror of PoVendorsPage (the vendor admin page) against
// the SC-S3c /api/subcontracts/* routes. cap.subcontracts.manage drives the write affordances (the
// Worker re-gates every call). List + create + per-card edit + DEACTIVATE-never-delete (D4:
// `active: 0` through the same full-field update route). VISUAL: identical dash look — the operator
// design-language buttons (add=`.btn--primary`, edit=`.btn--edit`, deactivate=`.btn--retire`
// two-step armed, cancel=`.btn--secondary`) + PageShell.
//
// HEADLINE DELTA vs PoVendorsPage (flat grid): the directory is GROUPED BY STATE — one
// `.dash-section` per state (alphabetical by USPS code, blank collates last under "Unassigned"),
// each holding a `.dash-grid` of subcontractor cards. Data-model deltas: region→state (2-letter
// USPS, dropdown of US_STATES), supply_categories→trades (free-string chips), one gtc_reference→
// three refs (msa_reference + NEW coi_reference + NEW license_number).
//
// §51 up-sync visibility: a row with sync_state === 'pending' is a portal edit the Mac-side daemon
// hasn't mirrored to ITS_Subcontractors yet — it carries a "Syncing to Smartsheet" badge.

type FormState = {
  sub_name: string;
  address: string;
  contact_name: string;
  contact_email: string;
  contact_phone: string;
  state: string;
  trades: string[];
  default_terms_profile: string;
  msa_reference: string;
  coi_reference: string;
  license_number: string;
  notes: string;
};
const EMPTY_FORM: FormState = {
  sub_name: "",
  address: "",
  contact_name: "",
  contact_email: "",
  contact_phone: "",
  state: "",
  trades: [],
  default_terms_profile: "",
  msa_reference: "",
  coi_reference: "",
  license_number: "",
  notes: "",
};

function toFields(f: FormState, active: number): api.SubcontractorFields {
  return {
    sub_name: f.sub_name.trim(),
    address: f.address.trim(),
    contact_name: f.contact_name.trim(),
    contact_email: f.contact_email.trim(),
    contact_phone: f.contact_phone.trim(),
    state: f.state,
    trades: f.trades,
    default_terms_profile: f.default_terms_profile,
    msa_reference: f.msa_reference.trim(),
    coi_reference: f.coi_reference.trim(),
    license_number: f.license_number.trim(),
    notes: f.notes.trim(),
    active,
  };
}

function fromSubcontractor(s: api.Subcontractor): FormState {
  return {
    sub_name: s.sub_name,
    address: s.address,
    contact_name: s.contact_name,
    contact_email: s.contact_email,
    contact_phone: s.contact_phone,
    state: s.state,
    trades: s.trades,
    default_terms_profile: s.default_terms_profile,
    msa_reference: s.msa_reference,
    coi_reference: s.coi_reference,
    license_number: s.license_number,
    notes: s.notes,
  };
}

function FieldInput({
  label,
  value,
  onChange,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
}) {
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      <input className="field__input" type={type} value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

/** Shared create/edit form body — the subcontractor field set from the ITS_Subcontractors schema. */
function SubcontractorFormFields({
  f,
  set,
  terms,
}: {
  f: FormState;
  set: (next: FormState) => void;
  terms: api.TermsProfile[];
}) {
  const toggleTrade = (t: string) =>
    set({
      ...f,
      trades: f.trades.includes(t) ? f.trades.filter((x) => x !== t) : [...f.trades, t],
    });
  return (
    <>
      <FieldInput label="Subcontractor name (required)" value={f.sub_name} onChange={(v) => set({ ...f, sub_name: v })} />
      <FieldInput label="Address (party block, 1–4 lines comma-separated)" value={f.address} onChange={(v) => set({ ...f, address: v })} />
      <FieldInput label="Contact name" value={f.contact_name} onChange={(v) => set({ ...f, contact_name: v })} />
      <FieldInput label="Contact email (the send-time recipient)" value={f.contact_email} onChange={(v) => set({ ...f, contact_email: v })} />
      <FieldInput label="Contact phone" value={f.contact_phone} onChange={(v) => set({ ...f, contact_phone: v })} />
      <label className="field">
        <span className="field__label">State</span>
        <select className="field__input" value={f.state} onChange={(e) => set({ ...f, state: e.target.value })}>
          <option value="">— state —</option>
          {US_STATES.map(([code, name]) => (
            <option key={code} value={code}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <div className="field">
        <span className="field__label">Trades</span>
        <div className="mats-cat-filter" role="group" aria-label="Trades">
          {TRADES.map((t) => (
            <button
              key={t}
              type="button"
              className={`mats-cat-filter__chip${f.trades.includes(t) ? " mats-cat-filter__chip--active" : ""}`}
              aria-pressed={f.trades.includes(t)}
              onClick={() => toggleTrade(t)}
            >
              {t}
            </button>
          ))}
        </div>
      </div>
      <label className="field">
        <span className="field__label">Default terms profile</span>
        <select
          className="field__input"
          value={f.default_terms_profile}
          onChange={(e) => set({ ...f, default_terms_profile: e.target.value })}
        >
          <option value="">— none —</option>
          {terms.map((t) => (
            <option key={t.id} value={t.id}>
              {t.label}
            </option>
          ))}
        </select>
      </label>
      <FieldInput label="MSA reference (negotiated-MSA subs: the Box link)" value={f.msa_reference} onChange={(v) => set({ ...f, msa_reference: v })} />
      <FieldInput label="COI reference (Box link — pointer only, no coverage gate)" value={f.coi_reference} onChange={(v) => set({ ...f, coi_reference: v })} />
      <FieldInput label="License # (state license board — free text)" value={f.license_number} onChange={(v) => set({ ...f, license_number: v })} />
      <FieldInput label="Notes" value={f.notes} onChange={(v) => set({ ...f, notes: v })} />
    </>
  );
}

/** Ordered [state, subs[]] groups — the headline delta from the flat PO grid. State uppercased;
 *  blank ("") collates LAST under an "Unassigned" bucket; the rest alphabetical by USPS code. Rows
 *  already arrive sub_name ASC from the Worker, so within-group order is preserved. */
function groupByState(subs: api.Subcontractor[]): [string, api.Subcontractor[]][] {
  const m = new Map<string, api.Subcontractor[]>();
  for (const s of subs) {
    const key = (s.state ?? "").toUpperCase();
    const arr = m.get(key) ?? [];
    arr.push(s);
    m.set(key, arr);
  }
  return [...m.entries()].sort(([a], [b]) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b)));
}

export function SubcontractorsPage({ onBack }: { onBack: () => void }) {
  const { user } = useAuth();
  const caps = user?.capabilities ?? [];
  const canManage = caps.includes("cap.subcontracts.manage"); // UI affordance only — the Worker re-gates

  const [subcontractors, setSubcontractors] = useState<api.Subcontractor[]>([]);
  const [terms, setTerms] = useState<api.TermsProfile[]>([]);
  const [showInactive, setShowInactive] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [cf, setCf] = useState<FormState>({ ...EMPTY_FORM });
  const [editKey, setEditKey] = useState<string | null>(null);
  const [ef, setEf] = useState<FormState>({ ...EMPTY_FORM });
  /** Two-step armed deactivate — the sub_key whose Deactivate is armed. */
  const [armedKey, setArmedKey] = useState<string | null>(null);

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .fetchSubcontractors(showInactive)
      .then(setSubcontractors)
      .catch(() => setError("Failed to load the subcontractor list."))
      .finally(() => setLoading(false));
  }, [showInactive]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    // Terms profiles feed the default-terms select; a miss degrades to "— none —" only.
    api
      .fetchSubTerms()
      .then(setTerms)
      .catch(() => setTerms([]));
  }, []);

  async function submitCreate(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!cf.sub_name.trim()) {
      setMsg({ ok: false, text: "The subcontractor name is required." });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const { sub_key } = await api.createSubcontractor(toFields(cf, 1));
      setCf({ ...EMPTY_FORM });
      setCreateOpen(false);
      reload();
      setMsg({ ok: true, text: `Subcontractor added (${sub_key}).` });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Create failed." });
    } finally {
      setBusy(false);
    }
  }

  function openEdit(s: api.Subcontractor) {
    setEditKey(s.sub_key);
    setEf(fromSubcontractor(s));
    setArmedKey(null);
  }

  async function saveEdit(s: api.Subcontractor) {
    if (busy) return;
    if (!ef.sub_name.trim()) {
      setMsg({ ok: false, text: "The subcontractor name is required." });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await api.updateSubcontractor(s.sub_key, toFields(ef, s.active)); // active preserved — never flipped by an edit
      setEditKey(null);
      reload();
      setMsg({ ok: true, text: "Subcontractor updated." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setBusy(false);
    }
  }

  /** Deactivate / reactivate ride the same full-field update route with active flipped —
   *  NEVER a delete (D4). The current field values are re-sent verbatim. */
  async function setActive(s: api.Subcontractor, active: number) {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    setArmedKey(null);
    try {
      await api.updateSubcontractor(s.sub_key, toFields(fromSubcontractor(s), active));
      reload();
      setMsg({ ok: true, text: active === 1 ? "Subcontractor reactivated." : "Subcontractor deactivated." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setBusy(false);
    }
  }

  const termsLabel = (id: string) => terms.find((t) => t.id === id)?.label ?? id;

  const renderCard = (s: api.Subcontractor) => (
    <section key={s.sub_key} className={`card${s.active ? "" : " po-vendor--inactive"}`}>
      <div className="dash-card__head">
        <h3 className="dash-card__title">{s.sub_name}</h3>
        {s.active ? null : <span className="dash-pill dash-pill--warn">Inactive</span>}
        {s.sync_state === "pending" ? (
          <span className="dash-pill" title="A portal edit not yet mirrored to the ITS_Subcontractors sheet.">
            Syncing to Smartsheet
          </span>
        ) : null}
      </div>
      <div className="dash-card__sub">{s.sub_key}</div>

      <div className="dash-card__row">
        <div className="dash-chips">
          {s.state ? <span className="dash-chip">{s.state}</span> : null}
          {s.trades.map((t) => (
            <span key={t} className="dash-chip">
              {t}
            </span>
          ))}
        </div>
      </div>

      {s.contact_name || s.contact_email || s.contact_phone ? (
        <div className="dash-card__row">
          <span className="dash-card__label">Contact</span>
          <span>{[s.contact_name, s.contact_email, s.contact_phone].filter(Boolean).join(" · ")}</span>
        </div>
      ) : null}
      {s.address ? (
        <div className="dash-card__row">
          <span className="dash-card__label">Address</span>
          <span>{s.address}</span>
        </div>
      ) : null}
      {s.default_terms_profile ? (
        <div className="dash-card__row">
          <span className="dash-card__label">Terms</span>
          <span>{termsLabel(s.default_terms_profile)}</span>
        </div>
      ) : null}
      {s.msa_reference ? (
        <div className="dash-card__row">
          <span className="dash-card__label">MSA</span>
          <span>{s.msa_reference}</span>
        </div>
      ) : null}
      {s.coi_reference ? (
        <div className="dash-card__row">
          <span className="dash-card__label">COI</span>
          <span>{s.coi_reference}</span>
        </div>
      ) : null}
      {s.license_number ? (
        <div className="dash-card__row">
          <span className="dash-card__label">License #</span>
          <span>{s.license_number}</span>
        </div>
      ) : null}
      {s.notes ? (
        <div className="dash-card__row">
          <span className="dash-card__label">Notes</span>
          <span>{s.notes}</span>
        </div>
      ) : null}

      {canManage && editKey !== s.sub_key ? (
        <div className="dash-row">
          <button className="btn btn--edit" onClick={() => openEdit(s)}>
            Edit
          </button>
          {s.active ? (
            armedKey === s.sub_key ? (
              <button className="btn btn--retire" disabled={busy} onClick={() => void setActive(s, 0)}>
                Confirm deactivate
              </button>
            ) : (
              <button className="btn btn--retire" onClick={() => setArmedKey(s.sub_key)}>
                Deactivate
              </button>
            )
          ) : (
            <button className="btn btn--edit" disabled={busy} onClick={() => void setActive(s, 1)}>
              Reactivate
            </button>
          )}
        </div>
      ) : null}

      {canManage && editKey === s.sub_key ? (
        <div className="accounts__editor">
          <SubcontractorFormFields f={ef} set={setEf} terms={terms} />
          <div className="jha__actions">
            <button className="btn btn--primary" disabled={busy} onClick={() => void saveEdit(s)}>
              {busy ? "Saving…" : "Save"}
            </button>
            <button className="btn btn--secondary" onClick={() => setEditKey(null)}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );

  return (
    <PageShell onHome={onBack}>
      <h2 className="page__heading">Subcontractors</h2>
      <p className="dash__intro">
        The subcontractor directory behind subcontract packages — the subcontract builder's picker
        draws from these, and the send path resolves each subcontract's recipient from the
        subcontractor's contact email. Subcontractors are deactivated, never deleted.
      </p>

      {msg && <div className={`banner ${msg.ok ? "banner--ok" : "banner--err"}`}>{msg.text}</div>}
      {error && <div className="banner banner--err">{error}</div>}

      <label className="field">
        <span className="field__label">
          <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} /> Show inactive
        </span>
      </label>

      {canManage && (
        <section className="card dash-section">
          {!createOpen ? (
            <button
              className="btn btn--primary"
              onClick={() => {
                setCreateOpen(true);
                setCf({ ...EMPTY_FORM });
              }}
            >
              + Add a subcontractor
            </button>
          ) : (
            <form onSubmit={submitCreate}>
              <h3 className="dash-detail__h2">Add a subcontractor</h3>
              <SubcontractorFormFields f={cf} set={setCf} terms={terms} />
              <div className="jha__actions">
                <button className="btn btn--primary" type="submit" disabled={busy}>
                  {busy ? "Working…" : "Add subcontractor"}
                </button>
                <button className="btn btn--secondary" type="button" onClick={() => setCreateOpen(false)}>
                  Cancel
                </button>
              </div>
            </form>
          )}
        </section>
      )}

      {loading && subcontractors.length === 0 ? (
        <p className="muted">Loading…</p>
      ) : subcontractors.length === 0 ? (
        <div className="dash-empty">No subcontractors.</div>
      ) : (
        groupByState(subcontractors).map(([state, subs]) => (
          <section key={state || "_none"} className="dash-section">
            <h3 className="dash-detail__h2">
              {state ? stateName(state) : "Unassigned"} <span className="dash-pill">{subs.length}</span>
            </h3>
            <div className="dash-grid">{subs.map(renderCard)}</div>
          </section>
        ))
      )}
    </PageShell>
  );
}
