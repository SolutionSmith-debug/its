import { useState, useEffect, useCallback } from "react";
import * as api from "../lib/po";
import { PageShell } from "../components/PageShell";

// PO Configuration (Administration) — a READ-ONLY window onto the three config classes that
// print on every purchase order: the Purchaser identity (D5), the ship-to-state tax table (D8),
// and the terms-library profiles (D6/S3). It reads the SAME session + cap.po.manage routes the
// builder already uses — GET /api/po/config (purchaser + tax) and GET /api/po/terms (the curated
// profile view) — so it needs no new Worker surface.
//
// WHY READ-ONLY (the §50 boundary, stated in the UI): these values live in version-controlled
// config (po_materials/config/*.json) and sha256-PINNED terms files (po_materials/terms/*.md) —
// editing them is a privileged code-actuation (Operational Standards §50) with a legal-review gate
// on the terms text, deliberately NOT a portal write. This page gives the office VISIBILITY into
// what every PO carries (so a wrong entity / tax rate / terms version is caught by eye) while the
// actual edit stays an operator/Seth action. The edit/actuator design is queued as its own slice
// (ADR-0002) so the first git-committing config actuation happens with the operator present.
//
// VISUAL: the same URS-Marine dash look as the Materials Catalog and Vendors admin pages —
// `.card dash-section` blocks with gold-underlined `.jha__section-title` heads, `.dash-chip`
// metadata chips, and the design-language back/home shell (PageShell).

/** Basis points → a fixed 2-decimal percent string (900 → "9.00%"). Integer-safe display. */
function bpToPct(bp: number): string {
  return `${(bp / 100).toFixed(2)}%`;
}

export function PoConfigPage({ onBack }: { onBack: () => void }) {
  const [config, setConfig] = useState<api.PoConfig | null>(null);
  const [terms, setTerms] = useState<api.TermsProfile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

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

  return (
    <PageShell onHome={onBack}>
      <h2 className="page__heading">PO Configuration</h2>
      <p className="muted po-config__intro">
        Read-only view of the identity, tax, and terms values that print on every purchase order.
        These are set in the ITS configuration — editing them is an operator action with a legal
        review on the terms text, not a portal edit.
      </p>

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
                      <div className="po-config__tax-rate">
                        {bp == null ? "—" : bpToPct(bp)}
                      </div>
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
          </section>
        </>
      )}
    </PageShell>
  );
}
