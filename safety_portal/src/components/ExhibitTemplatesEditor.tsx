import { useState, useEffect, useCallback } from "react";
import type { FormEvent } from "react";
import type { ExhibitTemplateSummary, ExhibitKeyText, ExhibitKeyVersions } from "../lib/subcontracts";

/**
 * ExhibitTemplatesEditor (PR-B2) — the config-editor block for the per-trade Exhibit A Article II
 * ("The Work") templates. Keyed by template KEY (the Trades fan onto a fixed set of keys). Two
 * send-free edit forms:
 *  - Add a version — mints a NEW Article II version with legal_review: pending (add_version).
 *  - Make a version current — the Layer-A legal ACTIVATION: clears legal_review + repoints the key's
 *    current_version so new subcontracts render it (set_current). Only QUEUED here — the Mac config
 *    daemon validates + deploys. The Worker re-gates every write (Invariant 2); SPA gating is convenience.
 *
 * Differs from TermsProfilesEditor: keyed by template_key (not a profile id), a FIXED key vocabulary
 * (so NO create), no attach kind. workstream is always "subcontracts", artifact "exhibit".
 */

interface ExhibitEditBody {
  workstream: string;
  artifact_key: string;
  op: "add_version" | "set_current";
  payload: unknown;
  target_version?: string;
}

interface Props {
  canManage: boolean;
  busy: boolean;
  setBusy: (b: boolean) => void;
  fetchKeys: () => Promise<ExhibitTemplateSummary[]>;
  fetchText: (key: string, version?: string) => Promise<ExhibitKeyText>;
  fetchVersions: (key: string) => Promise<ExhibitKeyVersions>;
  submitConfigEdit: (body: ExhibitEditBody) => Promise<unknown>;
  setMsg: (m: { ok: boolean; text: string } | null) => void;
  onQueued: () => void;
}

type AddForm = { template_key: string; target_version: string; text: string };
type MakeCurrentForm = { template_key: string; target_version: string; confirmed: boolean };

const TOKEN_RE = /\{\{[a-z_]+\}\}/;

export function ExhibitTemplatesEditor({
  canManage,
  busy,
  setBusy,
  fetchKeys,
  fetchText,
  fetchVersions,
  submitConfigEdit,
  setMsg,
  onQueued,
}: Props) {
  const [templates, setTemplates] = useState<ExhibitTemplateSummary[]>([]);
  const [addOpen, setAddOpen] = useState(false);
  const [af, setAf] = useState<AddForm>({ template_key: "", target_version: "", text: "" });
  const [makeCurrentOpen, setMakeCurrentOpen] = useState(false);
  const [mcf, setMcf] = useState<MakeCurrentForm>({ template_key: "", target_version: "", confirmed: false });
  const [mcVersions, setMcVersions] = useState<{ version: string; legal_review: string }[]>([]);
  const [mcCurrent, setMcCurrent] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchKeys()
      .then(setTemplates)
      .catch(() => setTemplates([]));
  }, [fetchKeys]);
  useEffect(() => {
    load();
  }, [load]);

  // Pre-fill the textarea with a key's CURRENT Article II so the operator edits from the live wording.
  async function loadAddText(key: string) {
    try {
      const { article_ii } = await fetchText(key);
      setAf((cur) => ({ ...cur, template_key: key, text: article_ii }));
    } catch {
      setAf((cur) => ({ ...cur, template_key: key, text: "" }));
    }
  }

  function openAdd() {
    const first = templates[0]?.template_key ?? "";
    setAf({ template_key: first, target_version: "", text: "" });
    setMsg(null);
    setMakeCurrentOpen(false);
    setAddOpen(true);
    if (first) void loadAddText(first);
  }

  async function submitAdd(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const template_key = af.template_key.trim();
    const targetVersion = af.target_version.trim();
    const text = af.text;
    if (!template_key) {
      setMsg({ ok: false, text: "Pick a trade template." });
      return;
    }
    if (!/^[a-z0-9_]+$/.test(targetVersion) || targetVersion.length > 64) {
      setMsg({ ok: false, text: "The version name must be lowercase letters, numbers, and underscores (e.g. v2)." });
      return;
    }
    if (!text.trim()) {
      setMsg({ ok: false, text: "Enter the Article II scope text for this version." });
      return;
    }
    if (TOKEN_RE.test(text)) {
      setMsg({ ok: false, text: "Article II must not contain {{tokens}} — it is the literal scope body, not a template." });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await submitConfigEdit({
        workstream: "subcontracts",
        artifact_key: "exhibit",
        op: "add_version",
        payload: { template_key, text },
        target_version: targetVersion,
      });
      setAddOpen(false);
      setMsg({
        ok: true,
        text: "Queued — the new Article II version will be minted with legal_review: pending. Track it below.",
      });
      onQueued();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Submit failed." });
    } finally {
      setBusy(false);
    }
  }

  async function loadMcVersions(key: string) {
    try {
      const v = await fetchVersions(key);
      setMcVersions(v.versions);
      setMcCurrent(v.current_version);
      const firstOther = v.versions.find((x) => x.version !== v.current_version)?.version;
      setMcf({ template_key: key, target_version: firstOther ?? v.current_version ?? "", confirmed: false });
    } catch {
      setMcVersions([]);
      setMcCurrent(null);
      setMcf({ template_key: key, target_version: "", confirmed: false });
    }
  }

  function openMakeCurrent() {
    const first = templates[0]?.template_key ?? "";
    setMcf({ template_key: first, target_version: "", confirmed: false });
    setMcVersions([]);
    setMcCurrent(null);
    setMsg(null);
    setAddOpen(false);
    setMakeCurrentOpen(true);
    if (first) void loadMcVersions(first);
  }

  async function submitMakeCurrent(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!mcf.template_key) {
      setMsg({ ok: false, text: "Pick a trade template." });
      return;
    }
    if (!mcf.target_version) {
      setMsg({ ok: false, text: "Pick a version to make current." });
      return;
    }
    if (!mcf.confirmed) {
      setMsg({ ok: false, text: "Confirm you have reviewed this version's Article II before making it live." });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await submitConfigEdit({
        workstream: "subcontracts",
        artifact_key: "exhibit",
        op: "set_current",
        payload: { template_key: mcf.template_key },
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

  return (
    <section className="card dash-section" aria-label="Exhibit A trade templates">
      <h3 className="jha__section-title">
        Exhibit A — Article II templates <span className="dash-pill">{templates.length}</span>
      </h3>
      {templates.length === 0 ? (
        <p className="muted">No trade templates configured.</p>
      ) : (
        <div className="dash-grid">
          {templates.map((t) => (
            <section key={t.template_key} className="card" aria-label={`${t.template_key} exhibit template`}>
              <div className="po-config__terms-head">
                <strong>{t.template_key}</strong>
              </div>
              <div className="dash-chips">
                <span className="dash-chip">current: {t.current_version}</span>
                {t.versions.map((v) => (
                  <span key={v.version} className="dash-chip">
                    {v.version}: {v.legal_review}
                  </span>
                ))}
              </div>
              {t.trades.length > 0 && (
                <div className="po-config__block">
                  <div className="field__label">Trades</div>
                  <div className="dash-chips">
                    {t.trades.map((tr) => (
                      <span key={tr} className="dash-chip">
                        {tr}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </section>
          ))}
        </div>
      )}

      {canManage &&
        (addOpen ? (
          <form className="accounts__editor" onSubmit={submitAdd}>
            <p className="po-config__legal-note" role="note">
              <strong>Legal-review gate.</strong> This mints a NEW Article II version with{" "}
              <code>legal_review: pending</code>. It is NOT used on any subcontract until you make it current.
            </p>
            <label className="field">
              <span className="field__label">Trade template</span>
              <select
                className="field__input"
                aria-label="Trade template"
                value={af.template_key}
                onChange={(e) => void loadAddText(e.target.value)}
              >
                {templates.map((t) => (
                  <option key={t.template_key} value={t.template_key}>
                    {t.template_key}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field__label">New version name (lowercase, e.g. v2)</span>
              <input
                className="field__input"
                value={af.target_version}
                placeholder="v2"
                onChange={(e) => setAf({ ...af, target_version: e.target.value })}
              />
            </label>
            <label className="field">
              <span className="field__label">Article II — the Work (scope text)</span>
              <textarea
                className="field__textarea"
                aria-label="Article II scope text"
                value={af.text}
                rows={10}
                maxLength={100000}
                onChange={(e) => setAf({ ...af, text: e.target.value })}
              />
            </label>
            <div className="jha__actions">
              <button className="btn btn--primary" type="submit">
                {busy ? "Working…" : "Queue new version"}
              </button>
              <button className="btn btn--secondary" type="button" onClick={() => setAddOpen(false)}>
                Cancel
              </button>
            </div>
          </form>
        ) : makeCurrentOpen ? (
          <form className="accounts__editor" onSubmit={submitMakeCurrent}>
            <p className="po-config__legal-note" role="note">
              <strong>Legal activation.</strong> Making a version current CLEARS its legal review and points
              the template&rsquo;s <code>current_version</code> at it, so new subcontracts render it. Confirm
              you have reviewed the exact Article II before making it live.
            </p>
            <label className="field">
              <span className="field__label">Trade template</span>
              <select
                className="field__input"
                aria-label="Make-current trade template"
                value={mcf.template_key}
                onChange={(e) => void loadMcVersions(e.target.value)}
              >
                {templates.map((t) => (
                  <option key={t.template_key} value={t.template_key}>
                    {t.template_key}
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
                  aria-label="I have reviewed this version's Article II"
                  checked={mcf.confirmed}
                  onChange={(e) => setMcf({ ...mcf, confirmed: e.target.checked })}
                />{" "}
                I have reviewed this version&rsquo;s Article II — make it live.
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
            <button className="btn btn--edit" type="button" onClick={openAdd}>
              Add an Article II version
            </button>
            <button className="btn btn--edit" type="button" onClick={openMakeCurrent}>
              Make a version current
            </button>
          </div>
        ))}
    </section>
  );
}
