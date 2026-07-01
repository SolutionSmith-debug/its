import { useState, useEffect } from "react";
import * as api from "../lib/fieldops_equipment";
import { useAuth } from "../lib/auth";
import { PageShell } from "../components/PageShell";
import { EquipmentDetailView } from "./EquipmentDetailView";
import { EquipmentManageView } from "./EquipmentManageView";
import { fmtDateTime, fmtNumber, equipStatusLabel, equipStatusPillClass, type EquipmentListRow } from "./equipmentView";

/**
 * Field-Ops EQUIPMENT surface — a URS-Marine-style three-view refinement, self-contained inside
 * this one page (like FieldOpsJobTracker manages list/detail internally): a card-grid DASHBOARD →
 * click a unit → DETAIL (full history + field actions) → an admin MANAGE screen (roster add/edit/
 * retire). App.tsx/HomePage.tsx routing + the `{ onBack }` prop contract are unchanged; the
 * sub-views are sibling components, all reusing THIS portal's own data layer (fieldops_equipment.ts)
 * and cap gates (cap.equipment.field for operations, cap.equipment.manage for the roster).
 */
export function FieldOpsEquipment({ onBack }: { onBack: () => void }) {
  const { user } = useAuth();
  const canManage = (user?.capabilities ?? []).includes("cap.equipment.manage");

  const [view, setView] = useState<"dashboard" | "detail" | "manage">("dashboard");
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const [equipment, setEquipment] = useState<EquipmentListRow[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void loadList();
  }, []);

  async function loadList() {
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.fetchEquipmentList(cursor || undefined);
      setEquipment((prev) => [...prev, ...data.equipment]);
      setCursor(data.next_cursor);
    } catch {
      setError("Failed to load equipment.");
    } finally {
      setLoading(false);
    }
  }

  async function loadMore() {
    if (!cursor || loading) return;
    await loadList();
  }

  // Replace-style refresh (loadList APPENDS; this resets to page 1). Used when returning from a
  // detail/manage view so a status change / retire is reflected on the dashboard cards.
  async function reloadList() {
    try {
      const data = await api.fetchEquipmentList(undefined);
      setEquipment(data.equipment);
      setCursor(data.next_cursor);
    } catch {
      setError("Failed to load equipment.");
    }
  }

  function openDetail(id: number) {
    setSelectedId(id);
    setError(null);
    setView("detail");
  }

  function backToDashboard() {
    setView("dashboard");
    setSelectedId(null);
    setError(null);
    void reloadList();
  }

  if (view === "manage") {
    return <EquipmentManageView onBack={backToDashboard} onHome={onBack} />;
  }
  if (view === "detail" && selectedId != null) {
    return <EquipmentDetailView id={selectedId} onBack={backToDashboard} onHome={onBack} />;
  }

  // ── DASHBOARD (card grid) ──
  return (
    <PageShell onHome={onBack}>
      <h2 className="page__heading">Equipment</h2>
      <p className="dash__intro">
        Readiness + point-in-time status per unit — last known location, latest inspection, and recent
        machine logs. Select a unit for its full history and field actions.
      </p>

      {canManage && (
        <div className="dash-toolbar">
          <button type="button" className="btn--primary" onClick={() => setView("manage")}>
            Manage equipment
          </button>
        </div>
      )}

      {error && <div className="banner banner--err">{error}</div>}

      {equipment.length === 0 ? (
        loading ? (
          <div className="muted">Loading equipment…</div>
        ) : (
          <div className="dash-unavail">No active equipment.</div>
        )
      ) : (
        <>
          <div className="dash-grid">
            {equipment.map((eq) => (
              <EquipmentCard key={eq.id} eq={eq} onOpen={() => openDetail(eq.id)} />
            ))}
          </div>
          {cursor && (
            <div className="dash-row dash-load-more">
              <button onClick={loadMore} disabled={loading} className="btn--secondary">
                {loading ? "Loading..." : "Load more"}
              </button>
            </div>
          )}
        </>
      )}
    </PageShell>
  );
}

/** One dashboard card — readiness pill, point-in-time location, latest inspection, recent logs.
 *  Keyboard-operable (role/tabIndex + Enter/Space), mirroring the URS EquipmentDashboard card. */
function EquipmentCard({ eq, onOpen }: { eq: EquipmentListRow; onOpen: () => void }) {
  return (
    <section
      className="card dash-card--click equip-card"
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
    >
      <div className="dash-card__head">
        <h3 className="dash-card__title">{eq.name}</h3>
        <span className={equipStatusPillClass(eq.status)}>{equipStatusLabel(eq.status)}</span>
      </div>
      <div className="dash-card__sub">
        {eq.kind ?? "Equipment"} · {eq.identifier ?? `#${eq.id}`}
      </div>
      {eq.status_note && <div className="dash-card__sub">{eq.status_note}</div>}

      <div className="dash-card__row">
        <span className="dash-card__label">Location (point-in-time)</span>
        {eq.location ? (
          <span>
            {eq.location.label ?? (eq.location.job_id ? `Job ${eq.location.job_id}` : "Located")}
            {eq.location.recorded_at ? (
              <span className="dash-card__sub"> · {fmtDateTime(eq.location.recorded_at)}</span>
            ) : null}
          </span>
        ) : (
          <span className="dash-unavail">Unavailable — no live tracking</span>
        )}
      </div>

      <div className="dash-card__row">
        <span className="dash-card__label">Latest inspection</span>
        {eq.latest_inspection ? (
          <span>
            {eq.latest_inspection.form_code} v{eq.latest_inspection.version}
            <span className="dash-card__sub">
              {" "}
              · {fmtDateTime(eq.latest_inspection.performed_at ?? eq.latest_inspection.recorded_at)}
            </span>
          </span>
        ) : (
          <span className="dash-unavail">None recorded</span>
        )}
      </div>

      <div className="dash-card__row">
        <span className="dash-card__label">Recent logs</span>
        {eq.recent_logs.length ? (
          <ul className="dash-loglist">
            {eq.recent_logs.map((l) => (
              <li key={l.uuid}>
                <strong>{l.log_type}</strong>
                {l.value_num != null ? ` ${fmtNumber(l.value_num)}` : ""}
                {l.detail ? ` — ${l.detail}` : ""}
                <span className="dash-card__sub"> · {fmtDateTime(l.performed_at ?? l.recorded_at)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <span className="dash-unavail">None recorded</span>
        )}
      </div>
    </section>
  );
}
