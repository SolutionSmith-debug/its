import { useState } from "react";
import type { FormEvent } from "react";

/**
 * TermsProfilesEditor — the shared terms-library editor for the config page(s). Renders a workstream's
 * terms profiles (the display cards) and, for an admin who canManage, the three send-free edit forms:
 *  - Add a version (add_version) — mints a NEW version with legal_review: pending.
 *  - Make a version current (set_current) — the Layer-A legal ACTIVATION: clears legal_review +
 *    repoints current_version so new documents render it. The legally-significant step; only QUEUED
 *    here (a §50 config-request), never actuated — the Mac config daemon validates + deploys.
 *  - New terms profile (create_profile) — a brand-new library/attach profile.
 *
 * PARAMETERIZED over `workstream` (§14 — the PO and subcontract terms libraries are the SAME editor,
 * NOT a clone). The host page owns loading the profiles list, the busy flag, the result banner (setMsg),
 * and the config-status-monitor refresh (onQueued); every edit is a §50 config-request POST via the
 * injected submitConfigEdit. The Worker re-gates every write per workstream (Invariant 2) — the SPA
 * gating is convenience, never the boundary.
 */

/** The minimal profile shape the editor needs — both lib TermsProfile types satisfy this structurally. */
export interface EditorTermsProfile {
  id: string;
  kind: "library" | "attach";
  label: string;
  description?: string | null;
  current_version: string | null;
  tokens: string[];
  render_line: string | null;
}
export interface EditorTermsVersionRow {
  version: string;
  legal_review: string;
}
export interface TermsConfigEditBody {
  workstream: string;
  artifact_key: string;
  op: "add_version" | "set_current" | "create_profile";
  payload: unknown;
  target_version?: string;
}

interface Props {
  workstream: string;
  /** Section heading (e.g. "Terms & conditions profiles" / "Subcontract terms profiles"). */
  heading: string;
  terms: EditorTermsProfile[];
  canManage: boolean;
  busy: boolean;
  setBusy: (b: boolean) => void;
  fetchTermsText: (profileId: string) => Promise<{ text: string }>;
  fetchTermsVersions: (
    profileId: string,
  ) => Promise<{ versions: EditorTermsVersionRow[]; current_version: string | null }>;
  submitConfigEdit: (body: TermsConfigEditBody) => Promise<unknown>;
  /** The host page's shared result banner setter (null clears it). */
  setMsg: (m: { ok: boolean; text: string } | null) => void;
  /** Bump the host page's config-status monitor after a successful queue. */
  onQueued: () => void;
}

type TermsForm = { profile_id: string; target_version: string; text: string };
type MakeCurrentForm = { profile_id: string; target_version: string; confirmed: boolean };
type NewProfileForm = {
  profile_id: string;
  kind: "library" | "attach";
  label: string;
  description: string;
  version_id: string; // library only
  text: string; // library only
  render_line: string; // attach only
};

function FieldInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      <input className="field__input" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function FieldTextarea({
  label,
  value,
  onChange,
  rows = 8,
  maxLength = 8000,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  rows?: number;
  maxLength?: number;
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
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

export function TermsProfilesEditor({
  workstream,
  heading,
  terms,
  canManage,
  busy,
  setBusy,
  fetchTermsText,
  fetchTermsVersions,
  submitConfigEdit,
  setMsg,
  onQueued,
}: Props) {
  const [termsOpen, setTermsOpen] = useState(false);
  const [tf, setTf] = useState<TermsForm>({ profile_id: "", target_version: "", text: "" });
  const [makeCurrentOpen, setMakeCurrentOpen] = useState(false);
  const [mcf, setMcf] = useState<MakeCurrentForm>({ profile_id: "", target_version: "", confirmed: false });
  const [mcVersions, setMcVersions] = useState<EditorTermsVersionRow[]>([]);
  const [mcCurrent, setMcCurrent] = useState<string | null>(null);
  const [newProfileOpen, setNewProfileOpen] = useState(false);
  const emptyNpf: NewProfileForm = { profile_id: "", kind: "library", label: "", description: "", version_id: "v1", text: "", render_line: "" };
  const [npf, setNpf] = useState<NewProfileForm>(emptyNpf);

  // Only library profiles have versioned, editable text (attach profiles render a fixed line).
  const libraryTerms = terms.filter((t) => t.kind === "library");

  // Pre-fill the textarea with a profile's CURRENT version text so the operator edits from the live
  // wording rather than a blank box (they then save it as a NEW version via add_version). A failed
  // fetch / no-editable-text profile leaves the box empty — the operator can still type from scratch.
  async function loadTermsText(profileId: string) {
    try {
      const { text } = await fetchTermsText(profileId);
      setTf((cur) => ({ ...cur, profile_id: profileId, text }));
    } catch {
      setTf((cur) => ({ ...cur, profile_id: profileId, text: "" }));
    }
  }

  function openTerms() {
    const first = libraryTerms[0]?.id ?? "";
    setTf({ profile_id: first, target_version: "", text: "" });
    setMsg(null);
    setMakeCurrentOpen(false);
    setNewProfileOpen(false);
    setTermsOpen(true);
    if (first) void loadTermsText(first);
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
      await submitConfigEdit({
        workstream,
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
      onQueued();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Submit failed." });
    } finally {
      setBusy(false);
    }
  }

  // ── Make-current (the legal-activation op: clears legal_review + repoints current_version) ─────
  async function loadVersions(profileId: string) {
    try {
      const v = await fetchTermsVersions(profileId);
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
    setTermsOpen(false);
    setNewProfileOpen(false);
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
      await submitConfigEdit({
        workstream,
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
      onQueued();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Submit failed." });
    } finally {
      setBusy(false);
    }
  }

  // ── New terms profile (create_profile — mint a brand-new profile) ─────────────────────────────
  function openNewProfile() {
    setNpf(emptyNpf);
    setMsg(null);
    setTermsOpen(false);
    setMakeCurrentOpen(false);
    setNewProfileOpen(true);
  }

  async function submitNewProfile(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const profile_id = npf.profile_id.trim();
    const label = npf.label.trim();
    if (!/^[a-z0-9_]+$/.test(profile_id) || profile_id.length > 64) {
      setMsg({ ok: false, text: "The profile id must be lowercase letters, numbers, and underscores (e.g. vendor_acme)." });
      return;
    }
    if (!label) {
      setMsg({ ok: false, text: "Enter a short label for the profile." });
      return;
    }
    const description = npf.description.trim();
    let payload: Record<string, unknown>;
    if (npf.kind === "library") {
      const version_id = npf.version_id.trim();
      const text = npf.text.trim();
      if (!/^[a-z0-9_]+$/.test(version_id) || version_id.length > 64) {
        setMsg({ ok: false, text: "The initial version id must be lowercase letters, numbers, and underscores (e.g. v1)." });
        return;
      }
      if (!text) {
        setMsg({ ok: false, text: "Enter the clause text for the initial version." });
        return;
      }
      payload = { profile_id, kind: "library", label, version_id, text, ...(description ? { description } : {}) };
    } else {
      const render_line = npf.render_line.trim();
      if (!render_line) {
        setMsg({ ok: false, text: "Enter the reference line for the attached (negotiated) GTC." });
        return;
      }
      payload = { profile_id, kind: "attach", label, render_line, ...(description ? { description } : {}) };
    }
    setBusy(true);
    setMsg(null);
    try {
      await submitConfigEdit({ workstream, artifact_key: "terms", op: "create_profile", payload });
      setNewProfileOpen(false);
      setMsg({
        ok: true,
        text:
          npf.kind === "library"
            ? "Queued — the new profile will be minted with its first version legal_review: pending, and cannot render until you make it current. Track it below."
            : "Queued — the new attach profile will be minted. Track it below.",
      });
      onQueued();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Submit failed." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card dash-section" aria-label={heading}>
      <h3 className="jha__section-title">
        {heading} <span className="dash-pill">{terms.length}</span>
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
              <code>legal_review: pending</code>. It is NOT used until the operator clears the legal
              review and points the profile&rsquo;s <code>current_version</code> at it — the editor mints
              the version; activation is a separate operator step.
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
            <FieldTextarea label="Terms clause text" value={tf.text} onChange={(v) => setTf({ ...tf, text: v })} />
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
              <strong>Legal activation.</strong> Making a version current CLEARS its legal review and
              points the profile&rsquo;s <code>current_version</code> at it, so new documents render it.
              This is the legally-significant step — confirm you have reviewed the exact clause text
              before making it live.
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
        ) : newProfileOpen ? (
          <form className="accounts__editor" onSubmit={submitNewProfile}>
            <p className="po-config__legal-note" role="note">
              <strong>New terms profile.</strong> This mints a brand-new profile. A <strong>Library</strong>{" "}
              profile ships its first version with <code>legal_review: pending</code> — it is selectable
              but does NOT render until the operator makes a version current. An <strong>Attach</strong>{" "}
              profile is a fixed reference line to an externally-negotiated GTC.
            </p>
            <FieldInput
              label="Profile id (lowercase, e.g. vendor_acme)"
              value={npf.profile_id}
              placeholder="vendor_acme"
              onChange={(v) => setNpf({ ...npf, profile_id: v })}
            />
            <label className="field">
              <span className="field__label">Kind</span>
              <select
                className="field__input"
                aria-label="Profile kind"
                value={npf.kind}
                onChange={(e) => setNpf({ ...npf, kind: e.target.value === "attach" ? "attach" : "library" })}
              >
                <option value="library">Library — versioned clause text</option>
                <option value="attach">Attach — reference line to a negotiated GTC</option>
              </select>
            </label>
            <FieldInput label="Label" value={npf.label} placeholder="ACME vendor terms" onChange={(v) => setNpf({ ...npf, label: v })} />
            <FieldInput label="Description (optional)" value={npf.description} onChange={(v) => setNpf({ ...npf, description: v })} />
            {npf.kind === "library" ? (
              <>
                <FieldInput
                  label="Initial version id (lowercase, e.g. v1)"
                  value={npf.version_id}
                  placeholder="v1"
                  onChange={(v) => setNpf({ ...npf, version_id: v })}
                />
                <FieldTextarea
                  label="Initial version clause text"
                  value={npf.text}
                  onChange={(v) => setNpf({ ...npf, text: v })}
                />
              </>
            ) : (
              <FieldTextarea
                label="Reference line (rendered verbatim on the document)"
                value={npf.render_line}
                rows={3}
                maxLength={2000}
                onChange={(v) => setNpf({ ...npf, render_line: v })}
              />
            )}
            <div className="jha__actions">
              <button className="btn btn--primary" type="submit">
                {busy ? "Working…" : "Queue new profile"}
              </button>
              <button className="btn btn--secondary" type="button" onClick={() => setNewProfileOpen(false)}>
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
            <button className="btn btn--edit" type="button" onClick={openNewProfile}>
              New terms profile
            </button>
          </div>
        ))}
    </section>
  );
}
