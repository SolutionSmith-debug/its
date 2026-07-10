import { useState, useEffect, useCallback } from "react";
import type { FormEvent } from "react";
import * as api from "../lib/po";
import { SUPPLY_CATEGORIES, REGIONS, categoryLabel } from "../lib/po";
import { useAuth } from "../lib/auth";
import { PageShell } from "../components/PageShell";

// PO workstream S6 — vendor management (the MaterialsCatalogPage clone the delivery program
// names). cap.po.manage drives the write affordances (the Worker re-gates every call). List
// (active + greyed inactive via the toggle) + create + per-card edit + DEACTIVATE-never-delete
// (D4: `active: 0` through the same full-field update route). VISUAL: the refined dash look —
// `.dash-grid` of `.card` vendor cards (region/category `.dash-chip`s, inactive
// `.dash-pill--warn`), design-language buttons (add=`.btn--primary`, edit=`.btn--edit`,
// deactivate=`.btn--retire` two-step armed, cancel=`.btn--secondary`).
//
// §51 up-sync visibility: a row with sync_state === 'pending' is a portal edit the Mac-side
// daemon hasn't mirrored to ITS_Vendors yet — it carries a subtle "Syncing to Smartsheet"
// badge so the office knows the sheet may briefly lag the portal.

type FormState = {
  vendor_name: string;
  address: string;
  contact_name: string;
  contact_email: string;
  contact_phone: string;
  region: string;
  categories: string[];
  default_terms_profile: string;
  gtc_reference: string;
  notes: string;
};
const EMPTY_FORM: FormState = {
  vendor_name: "",
  address: "",
  contact_name: "",
  contact_email: "",
  contact_phone: "",
  region: "",
  categories: [],
  default_terms_profile: "",
  gtc_reference: "",
  notes: "",
};

function toFields(f: FormState, active: number): api.VendorFields {
  return {
    vendor_name: f.vendor_name.trim(),
    address: f.address.trim(),
    contact_name: f.contact_name.trim(),
    contact_email: f.contact_email.trim(),
    contact_phone: f.contact_phone.trim(),
    region: f.region,
    supply_categories: f.categories,
    default_terms_profile: f.default_terms_profile,
    gtc_reference: f.gtc_reference.trim(),
    notes: f.notes.trim(),
    active,
  };
}

function fromVendor(v: api.Vendor): FormState {
  return {
    vendor_name: v.vendor_name,
    address: v.address,
    contact_name: v.contact_name,
    contact_email: v.contact_email,
    contact_phone: v.contact_phone,
    region: v.region,
    categories: v.supply_categories,
    default_terms_profile: v.default_terms_profile,
    gtc_reference: v.gtc_reference,
    notes: v.notes,
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

/** Shared create/edit form body — the vendor field set from the ITS_Vendors schema. */
function VendorFormFields({
  f,
  set,
  terms,
}: {
  f: FormState;
  set: (next: FormState) => void;
  terms: api.TermsProfile[];
}) {
  const toggleCategory = (key: string) =>
    set({
      ...f,
      categories: f.categories.includes(key) ? f.categories.filter((c) => c !== key) : [...f.categories, key],
    });
  return (
    <>
      <FieldInput label="Vendor name (required)" value={f.vendor_name} onChange={(v) => set({ ...f, vendor_name: v })} />
      <FieldInput label="Address (as printed on the PO Seller block)" value={f.address} onChange={(v) => set({ ...f, address: v })} />
      <FieldInput label="Contact name" value={f.contact_name} onChange={(v) => set({ ...f, contact_name: v })} />
      <FieldInput label="Contact email (the send-time recipient)" value={f.contact_email} onChange={(v) => set({ ...f, contact_email: v })} />
      <FieldInput label="Contact phone" value={f.contact_phone} onChange={(v) => set({ ...f, contact_phone: v })} />
      <label className="field">
        <span className="field__label">Region</span>
        <select className="field__input" value={f.region} onChange={(e) => set({ ...f, region: e.target.value })}>
          <option value="">— region —</option>
          {REGIONS.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </label>
      <div className="field">
        <span className="field__label">Supply categories</span>
        <div className="mats-cat-filter" role="group" aria-label="Supply categories">
          {SUPPLY_CATEGORIES.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`mats-cat-filter__chip${f.categories.includes(key) ? " mats-cat-filter__chip--active" : ""}`}
              aria-pressed={f.categories.includes(key)}
              onClick={() => toggleCategory(key)}
            >
              {label}
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
      <FieldInput label="GTC reference (negotiated-GTC vendors: the Box link)" value={f.gtc_reference} onChange={(v) => set({ ...f, gtc_reference: v })} />
      <FieldInput label="Notes" value={f.notes} onChange={(v) => set({ ...f, notes: v })} />
    </>
  );
}

export function PoVendorsPage({ onBack }: { onBack: () => void }) {
  const { user } = useAuth();
  const caps = user?.capabilities ?? [];
  const canManage = caps.includes("cap.po.manage"); // UI affordance only — the Worker re-gates

  const [vendors, setVendors] = useState<api.Vendor[]>([]);
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
  /** Two-step armed deactivate — the vendor_key whose Deactivate is armed. */
  const [armedKey, setArmedKey] = useState<string | null>(null);

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .fetchVendors(showInactive)
      .then(setVendors)
      .catch(() => setError("Failed to load the vendor list."))
      .finally(() => setLoading(false));
  }, [showInactive]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    // Terms profiles feed the default-terms select; a miss degrades to "— none —" only.
    api
      .fetchTerms()
      .then(setTerms)
      .catch(() => setTerms([]));
  }, []);

  async function submitCreate(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!cf.vendor_name.trim()) {
      setMsg({ ok: false, text: "The vendor name is required." });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const { vendor_key } = await api.createVendor(toFields(cf, 1));
      setCf({ ...EMPTY_FORM });
      setCreateOpen(false);
      reload();
      setMsg({ ok: true, text: `Vendor added (${vendor_key}).` });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Create failed." });
    } finally {
      setBusy(false);
    }
  }

  function openEdit(v: api.Vendor) {
    setEditKey(v.vendor_key);
    setEf(fromVendor(v));
    setArmedKey(null);
  }

  async function saveEdit(v: api.Vendor) {
    if (busy) return;
    if (!ef.vendor_name.trim()) {
      setMsg({ ok: false, text: "The vendor name is required." });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await api.updateVendor(v.vendor_key, toFields(ef, v.active)); // active preserved — never flipped by an edit
      setEditKey(null);
      reload();
      setMsg({ ok: true, text: "Vendor updated." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setBusy(false);
    }
  }

  /** Deactivate / reactivate ride the same full-field update route with active flipped —
   *  NEVER a delete (D4). The current field values are re-sent verbatim. */
  async function setActive(v: api.Vendor, active: number) {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    setArmedKey(null);
    try {
      await api.updateVendor(v.vendor_key, toFields(fromVendor(v), active));
      reload();
      setMsg({ ok: true, text: active === 1 ? "Vendor reactivated." : "Vendor deactivated." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setBusy(false);
    }
  }

  const termsLabel = (id: string) => terms.find((t) => t.id === id)?.label ?? id;

  return (
    <PageShell onHome={onBack}>
      <h2 className="page__heading">Vendors</h2>
      <p className="dash__intro">
        The vendor directory behind purchase orders — the PO builder's vendor picker draws from these
        entries, and the send path resolves each PO's recipient from the vendor's contact email.
        Vendors are deactivated, never deleted.
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
              + Add a vendor
            </button>
          ) : (
            <form onSubmit={submitCreate}>
              <h3 className="dash-detail__h2">Add a vendor</h3>
              <VendorFormFields f={cf} set={setCf} terms={terms} />
              <div className="jha__actions">
                <button className="btn btn--primary" type="submit">
                  {busy ? "Working…" : "Add vendor"}
                </button>
                <button className="btn btn--secondary" type="button" onClick={() => setCreateOpen(false)}>
                  Cancel
                </button>
              </div>
            </form>
          )}
        </section>
      )}

      {loading && vendors.length === 0 ? (
        <p className="muted">Loading…</p>
      ) : vendors.length === 0 ? (
        <div className="dash-empty">No vendors.</div>
      ) : (
        <div className="dash-grid">
          {vendors.map((v) => (
            <section key={v.vendor_key} className={`card${v.active ? "" : " po-vendor--inactive"}`}>
              <div className="dash-card__head">
                <h3 className="dash-card__title">{v.vendor_name}</h3>
                {v.active ? null : <span className="dash-pill dash-pill--warn">Inactive</span>}
                {v.sync_state === "pending" ? (
                  <span className="dash-pill" title="A portal edit not yet mirrored to the ITS_Vendors sheet.">
                    Syncing to Smartsheet
                  </span>
                ) : null}
              </div>
              <div className="dash-card__sub">{v.vendor_key}</div>

              <div className="dash-card__row">
                <div className="dash-chips">
                  {v.region ? <span className="dash-chip">{v.region}</span> : null}
                  {v.supply_categories.map((c) => (
                    <span key={c} className="dash-chip">
                      {categoryLabel(c)}
                    </span>
                  ))}
                </div>
              </div>

              {v.contact_name || v.contact_email || v.contact_phone ? (
                <div className="dash-card__row">
                  <span className="dash-card__label">Contact</span>
                  <span>{[v.contact_name, v.contact_email, v.contact_phone].filter(Boolean).join(" · ")}</span>
                </div>
              ) : null}
              {v.address ? (
                <div className="dash-card__row">
                  <span className="dash-card__label">Address</span>
                  <span>{v.address}</span>
                </div>
              ) : null}
              {v.default_terms_profile ? (
                <div className="dash-card__row">
                  <span className="dash-card__label">Terms</span>
                  <span>{termsLabel(v.default_terms_profile)}</span>
                </div>
              ) : null}
              {v.gtc_reference ? (
                <div className="dash-card__row">
                  <span className="dash-card__label">GTC</span>
                  <span>{v.gtc_reference}</span>
                </div>
              ) : null}
              {v.notes ? (
                <div className="dash-card__row">
                  <span className="dash-card__label">Notes</span>
                  <span>{v.notes}</span>
                </div>
              ) : null}

              {canManage && editKey !== v.vendor_key ? (
                <div className="dash-row">
                  <button className="btn btn--edit" onClick={() => openEdit(v)}>
                    Edit
                  </button>
                  {v.active ? (
                    armedKey === v.vendor_key ? (
                      <button className="btn btn--retire" onClick={() => void setActive(v, 0)}>
                        Confirm deactivate
                      </button>
                    ) : (
                      <button className="btn btn--retire" onClick={() => setArmedKey(v.vendor_key)}>
                        Deactivate
                      </button>
                    )
                  ) : (
                    <button className="btn btn--edit" onClick={() => void setActive(v, 1)}>
                      Reactivate
                    </button>
                  )}
                </div>
              ) : null}

              {canManage && editKey === v.vendor_key ? (
                <div className="accounts__editor">
                  <VendorFormFields f={ef} set={setEf} terms={terms} />
                  <div className="jha__actions">
                    <button className="btn btn--primary" onClick={() => void saveEdit(v)}>
                      {busy ? "Saving…" : "Save"}
                    </button>
                    <button className="btn btn--secondary" onClick={() => setEditKey(null)}>
                      Cancel
                    </button>
                  </div>
                </div>
              ) : null}
            </section>
          ))}
        </div>
      )}
    </PageShell>
  );
}
