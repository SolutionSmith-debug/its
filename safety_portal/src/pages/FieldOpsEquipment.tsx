import { useState, useEffect } from "react";
import type { FormEvent } from "react";
import * as api from "../lib/fieldops_equipment";
import { useAuth } from "../lib/auth";
import { PageShell } from "../components/PageShell";

// Format helpers: epoch SECONDS (as stored in D1) → ×1000 for JS Date
function fmtDateTime(epochSeconds: number | null): string {
  if (!epochSeconds) return "—";
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleString();
}

function fmtNumber(val: number | null, digits = 2): string {
  if (val == null || isNaN(val)) return "—";
  return val.toFixed(digits);
}

// Pill class based on status snapshot
function equipStatusPillClass(status: api.EquipmentHeader["status"]): string {
  switch (status) {
    case "fmc": return "dash-pill--ok";
    case "degraded": return "dash-pill--warn";
    case "down": return "dash-pill--danger";
    default: return "";
  }
}

export function FieldOpsEquipment({ onBack }: { onBack: () => void }) {
  const [view, setView] = useState<"list" | "detail">("list");
  const [equipment, setEquipment] = useState<(api.EquipmentHeader & {
    location: api.LocationRecord | null;
    latest_inspection: api.InspectionRecord | null;
    recent_logs: api.LogRecord[];
  })[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Detail state
  const [selectedEquipment, setSelectedEquipment] = useState<api.EquipmentDetail | null>(null);
  const [locCursor, setLocCursor] = useState<string | null>(null);
  const [inspCursor, setInsppCursor] = useState<string | null>(null);
  const [logCursor, setLogCursor] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Write (P2.3 field actions; gated by cap.equipment.field)
  const { user } = useAuth();
  const canField = (user?.capabilities ?? []).includes("cap.equipment.field");
  const [statusVal, setStatusVal] = useState<api.EquipStatus>("fmc");
  const [statusNote, setStatusNote] = useState("");
  const [maintType, setMaintType] = useState<api.LogType>("maintenance");
  const [maintValue, setMaintValue] = useState("");
  const [maintDetail, setMaintDetail] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const [actionMsg, setActionMsg] = useState<{ ok: boolean; text: string } | null>(null);
  // Roster admin (cap.equipment.manage) + move (cap.equipment.field, job picker)
  const canManage = (user?.capabilities ?? []).includes("cap.equipment.manage");
  const [jobOptions, setJobOptions] = useState<api.JobOption[]>([]);
  const [moveJob, setMoveJob] = useState("");
  const [moveLabel, setMoveLabel] = useState("");
  const [newName, setNewName] = useState("");
  const [newKind, setNewKind] = useState("");
  const [newIdent, setNewIdent] = useState("");
  const [newStatus, setNewStatus] = useState<api.EquipStatus>("fmc");
  const [editName, setEditName] = useState("");
  const [editKind, setEditKind] = useState("");
  const [editIdent, setEditIdent] = useState("");
  const [editOpen, setEditOpen] = useState(false);

  useEffect(() => {
    loadList();
  }, []);

  useEffect(() => {
    if (canField) api.fetchActiveJobOptions().then(setJobOptions).catch(() => {});
  }, [canField]);

  async function loadList() {
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.fetchEquipmentList(cursor || undefined);
      setEquipment((prev) => [...prev, ...data.equipment]);
      setCursor(data.next_cursor);
    } catch (e) {
      setError("Failed to load equipment.");
    } finally {
      setLoading(false);
    }
  }

  async function loadMore() {
    if (!cursor || loading) return;
    await loadList();
  }

  function handleCardClick(eq: api.EquipmentHeader & {
    location: api.LocationRecord | null;
    latest_inspection: api.InspectionRecord | null;
    recent_logs: api.LogRecord[];
  }) {
    setView("detail");
    setSelectedEquipment(null);
    setDetailLoading(true);
    api
      .fetchEquipmentDetail(eq.id, undefined)
      .then((res) => {
        setSelectedEquipment(res.equipment);
        setLocCursor(res.cursors.loc);
        setInsppCursor(res.cursors.insp);
        setLogCursor(res.cursors.log);
        setDetailLoading(false);
      })
      .catch(() => setError("Failed to load equipment details."));
  }

  async function reloadDetail() {
    if (!selectedEquipment) return;
    const res = await api.fetchEquipmentDetail(selectedEquipment.header.id, undefined);
    setSelectedEquipment(res.equipment);
    setLocCursor(res.cursors.loc);
    setInsppCursor(res.cursors.insp);
    setLogCursor(res.cursors.log);
  }

  async function submitStatus(e: FormEvent) {
    e.preventDefault();
    if (!selectedEquipment || actionBusy) return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.setEquipmentStatus(selectedEquipment.header.id, {
        uuid: crypto.randomUUID(),
        status: statusVal,
        status_note: statusNote.trim() || undefined,
      });
      setStatusNote("");
      await reloadDetail();
      setActionMsg({ ok: true, text: "Readiness updated." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setActionBusy(false);
    }
  }

  async function submitMaint(e: FormEvent) {
    e.preventDefault();
    if (!selectedEquipment || actionBusy) return;
    const value = maintValue.trim() === "" ? undefined : Number(maintValue);
    if (value !== undefined && !Number.isFinite(value)) {
      setActionMsg({ ok: false, text: "Value must be a number." });
      return;
    }
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.logEquipmentMaintenance(selectedEquipment.header.id, {
        uuid: crypto.randomUUID(),
        log_type: maintType,
        value_num: value,
        detail: maintDetail.trim() || undefined,
      });
      setMaintValue("");
      setMaintDetail("");
      await reloadDetail();
      setActionMsg({ ok: true, text: "Log entry recorded." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Log failed." });
    } finally {
      setActionBusy(false);
    }
  }

  async function reloadList() {
    const data = await api.fetchEquipmentList(undefined);
    setEquipment(data.equipment);
    setCursor(data.next_cursor);
  }

  async function submitMove(e: FormEvent) {
    e.preventDefault();
    if (!selectedEquipment || actionBusy || !moveJob) return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.moveEquipment(selectedEquipment.header.id, { job_id: moveJob, label: moveLabel.trim() || undefined });
      setMoveLabel("");
      await reloadDetail();
      setActionMsg({ ok: true, text: "Location recorded." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Move failed." });
    } finally {
      setActionBusy(false);
    }
  }

  async function submitCreate(e: FormEvent) {
    e.preventDefault();
    if (actionBusy || newName.trim() === "") return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.createEquipment({ name: newName.trim(), kind: newKind.trim() || undefined, identifier: newIdent.trim() || undefined, status: newStatus });
      setNewName("");
      setNewKind("");
      setNewIdent("");
      await reloadList();
      setActionMsg({ ok: true, text: "Equipment added." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Add failed." });
    } finally {
      setActionBusy(false);
    }
  }

  function openEdit() {
    if (!selectedEquipment) return;
    const h = selectedEquipment.header;
    setEditName(h.name);
    setEditKind(h.kind ?? "");
    setEditIdent(h.identifier ?? "");
    setEditOpen(true);
  }

  async function submitEdit(e: FormEvent) {
    e.preventDefault();
    if (!selectedEquipment || actionBusy || editName.trim() === "") return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.updateEquipment(selectedEquipment.header.id, { name: editName.trim(), kind: editKind.trim() || undefined, identifier: editIdent.trim() || undefined });
      setEditOpen(false);
      await reloadDetail();
      setActionMsg({ ok: true, text: "Equipment updated." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
    } finally {
      setActionBusy(false);
    }
  }

  async function submitRetire() {
    if (!selectedEquipment || actionBusy) return;
    setActionBusy(true);
    setActionMsg(null);
    try {
      await api.retireEquipment(selectedEquipment.header.id);
      await reloadList();
      setActionBusy(false);
      handleBack(); // unit retired → back to the (refreshed) list
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Retire failed." });
      setActionBusy(false);
    }
  }

  function handleBack() {
    if (view === "detail") {
      setView("list");
      setLocCursor(null);
      setInsppCursor(null);
      setLogCursor(null);
      setSelectedEquipment(null);
      setError(null);
    } else {
      onBack();
    }
  }

  // Detail loading more for each leg
  async function loadMoreLocations() {
    if (!selectedEquipment || !locCursor) return;
    setDetailLoading(true);
    try {
      const res = await api.fetchEquipmentDetail(selectedEquipment.header.id, { loc: locCursor });
      setSelectedEquipment((prev) => {
        if (!prev) return res.equipment;
        // Prepend new locations (DESC order)
        const merged = [...res.equipment.locations, ...prev.locations];
        return { ...prev, locations: merged };
      });
      setLocCursor(res.cursors.loc);
    } catch {
      setError("Failed to load more locations.");
    } finally {
      setDetailLoading(false);
    }
  }

  async function loadMoreInspections() {
    if (!selectedEquipment || !inspCursor) return;
    setDetailLoading(true);
    try {
      const res = await api.fetchEquipmentDetail(selectedEquipment.header.id, { insp: inspCursor });
      setSelectedEquipment((prev) => {
        if (!prev) return res.equipment;
        // Prepend new inspections (DESC order)
        const merged = [...res.equipment.inspections, ...prev.inspections];
        return { ...prev, inspections: merged };
      });
      setInsppCursor(res.cursors.insp);
    } catch {
      setError("Failed to load more inspections.");
    } finally {
      setDetailLoading(false);
    }
  }

  async function loadMoreLogs() {
    if (!selectedEquipment || !logCursor) return;
    setDetailLoading(true);
    try {
      const res = await api.fetchEquipmentDetail(selectedEquipment.header.id, { log: logCursor });
      setSelectedEquipment((prev) => {
        if (!prev) return res.equipment;
        // Prepend new logs (DESC order)
        const merged = [...res.equipment.logs, ...prev.logs];
        return { ...prev, logs: merged };
      });
      setLogCursor(res.cursors.log);
    } catch {
      setError("Failed to load more logs.");
    } finally {
      setDetailLoading(false);
    }
  }

  if (view === "detail" && selectedEquipment) {
    const eq = selectedEquipment.header;
    return (
      <PageShell onHome={onBack}>
        <div className="dash-back-btn">
          <button onClick={handleBack} className="btn--secondary">
            ← Back to equipment
          </button>
        </div>

        <h2 className="page__heading">{eq.name}</h2>
        <p className="muted">
          {eq.kind ?? "Equipment"} • {eq.identifier ?? eq.id}
          {" • "}
          <span className={`dash-pill ${equipStatusPillClass(eq.status)}`}>
            {eq.status === "fmc" ? "Full Mission Capable" : eq.status === "degraded" ? "Degraded" : "Down"}
          </span>
        </p>
        {eq.status_note && <p className="muted">{eq.status_note}</p>}

        {canField && (
          <section className="card dash-section">
            <h3 className="dash-detail__h2">Field actions</h3>
            {actionMsg && (
              <p className="muted" style={{ color: actionMsg.ok ? "green" : "red" }}>{actionMsg.text}</p>
            )}
            <form onSubmit={submitStatus} className="dash-row" aria-label="Update readiness status">
              <label className="dash-card__label">
                Readiness:{" "}
                <select value={statusVal} onChange={(e) => setStatusVal(e.target.value as api.EquipStatus)}>
                  <option value="fmc">Full Mission Capable</option>
                  <option value="degraded">Degraded</option>
                  <option value="down">Down</option>
                </select>
              </label>{" "}
              <input value={statusNote} onChange={(e) => setStatusNote(e.target.value)} placeholder="Note (optional)" maxLength={512} />{" "}
              <button type="submit" disabled={actionBusy} className="btn--secondary">Update status</button>
            </form>
            <form onSubmit={submitMaint} className="dash-row" aria-label="Add machine log">
              <label className="dash-card__label">
                Log:{" "}
                <select value={maintType} onChange={(e) => setMaintType(e.target.value as api.LogType)}>
                  <option value="maintenance">Maintenance</option>
                  <option value="fuel">Fuel</option>
                  <option value="hours">Hours</option>
                </select>
              </label>{" "}
              <input value={maintValue} onChange={(e) => setMaintValue(e.target.value)} placeholder="Value (optional)" inputMode="decimal" />{" "}
              <input value={maintDetail} onChange={(e) => setMaintDetail(e.target.value)} placeholder="Detail (optional)" maxLength={2000} />{" "}
              <button type="submit" disabled={actionBusy} className="btn--secondary">Add log</button>
            </form>
            <form onSubmit={submitMove} className="dash-row" aria-label="Move equipment to a job">
              <label className="dash-card__label">
                Move to job:{" "}
                <select value={moveJob} onChange={(e) => setMoveJob(e.target.value)}>
                  <option value="">Select a job…</option>
                  {jobOptions.map((j) => (
                    <option key={j.job_id} value={j.job_id}>{j.project_name} ({j.job_id})</option>
                  ))}
                </select>
              </label>{" "}
              <input value={moveLabel} onChange={(e) => setMoveLabel(e.target.value)} placeholder="Site label (optional)" maxLength={256} />{" "}
              <button type="submit" disabled={actionBusy || !moveJob} className="btn--secondary">Record location</button>
            </form>
          </section>
        )}

        {canManage && (
          <section className="card dash-section">
            <h3 className="dash-detail__h2">Manage unit</h3>
            {editOpen ? (
              <form onSubmit={submitEdit} className="dash-row" aria-label="Edit equipment">
                <input value={editName} onChange={(e) => setEditName(e.target.value)} placeholder="Name" maxLength={128} />{" "}
                <input value={editKind} onChange={(e) => setEditKind(e.target.value)} placeholder="Kind (optional)" maxLength={64} />{" "}
                <input value={editIdent} onChange={(e) => setEditIdent(e.target.value)} placeholder="Identifier (optional)" maxLength={64} />{" "}
                <button type="submit" disabled={actionBusy} className="btn--secondary">Save</button>{" "}
                <button type="button" onClick={() => setEditOpen(false)} className="btn--secondary">Cancel</button>
              </form>
            ) : (
              <div className="dash-row">
                <button onClick={openEdit} className="btn--secondary">Edit details</button>{" "}
                <button onClick={submitRetire} disabled={actionBusy} className="btn--secondary">Retire unit</button>
              </div>
            )}
          </section>
        )}

        {/* Location */}
        <div className="dash-section">
          <h3 className="dash-detail__h2">Location history</h3>
          {selectedEquipment.locations.length === 0 ? (
            <div className="dash-empty">
              No location records. Click "Mark as unavailable" to indicate the unit is not on any job site.
            </div>
          ) : (
            <>
              <table className="dash-table">
                <thead>
                  <tr>
                    <th className="dash-header">Recorded</th>
                    <th className="dash-header">Label / Job</th>
                    <th className="dash-header">Lat</th>
                    <th className="dash-header">Lon</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedEquipment.locations.map((loc) => (
                    <tr key={loc.recorded_at + ":" + loc.id} className="dash-row">
                      <td className="dash-cell">{fmtDateTime(loc.recorded_at)}</td>
                      <td className="dash-cell">{loc.label ?? (loc.job_id ? `Job ${loc.job_id}` : "—")}</td>
                      <td className="dash-cell">{fmtNumber(loc.lat ?? 0, 4)}</td>
                      <td className="dash-cell">{fmtNumber(loc.lon ?? 0, 4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {locCursor && (
                <div className="dash-row dash-load-more">
                  <button
                    onClick={loadMoreLocations}
                    disabled={detailLoading}
                    className="btn--secondary"
                  >
                    {detailLoading ? "Loading..." : "Load more"}
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        {/* Inspections */}
        <div className="dash-section">
          <h3 className="dash-detail__h2">Inspections</h3>
          {selectedEquipment.inspections.length === 0 ? (
            <div className="dash-empty">No inspections recorded.</div>
          ) : (
            <>
              <table className="dash-table">
                <thead>
                  <tr>
                    <th className="dash-header">Form</th>
                    <th className="dash-header">Version</th>
                    <th className="dash-header">Performed at</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedEquipment.inspections.map((insp) => (
                    <tr key={insp.uuid} className="dash-row">
                      <td className="dash-cell">{insp.form_code}</td>
                      <td className="dash-cell">v{insp.version}</td>
                      <td className="dash-cell">{fmtDateTime(insp.performed_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {inspCursor && (
                <div className="dash-row dash-load-more">
                  <button
                    onClick={loadMoreInspections}
                    disabled={detailLoading}
                    className="btn--secondary"
                  >
                    {detailLoading ? "Loading..." : "Load more"}
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        {/* Logs */}
        <div className="dash-section">
          <h3 className="dash-detail__h2">Recent logs (max 5)</h3>
          {selectedEquipment.logs.length === 0 ? (
            <div className="dash-empty">No recent maintenance or status logs.</div>
          ) : (
            <>
              <ul className="dash-loglist">
                {selectedEquipment.logs.map((log) => (
                  <li key={log.uuid}>
                    <strong>{log.log_type}</strong>:
                    {" "}
                    {log.detail ?? (log.value_num != null ? `${fmtNumber(log.value_num)} ${log.status_value ?? ""}` : "—")}
                    {log.performed_at && <span className="muted"> • {fmtDateTime(log.performed_at)}</span>}
                  </li>
                ))}
              </ul>

              {logCursor && (
                <div className="dash-row dash-load-more">
                  <button
                    onClick={loadMoreLogs}
                    disabled={detailLoading}
                    className="btn--secondary"
                  >
                    {detailLoading ? "Loading..." : "Load more"}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </PageShell>
    );
  }

  // List view
  return (
    <PageShell onHome={onBack}>

      <h2 className="page__heading">Equipment</h2>
      {canManage && (
        <form onSubmit={submitCreate} className="dash-row" aria-label="Add equipment">
          {actionMsg && <p className="muted" style={{ color: actionMsg.ok ? "green" : "red" }}>{actionMsg.text}</p>}
          <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="New unit name" maxLength={128} />{" "}
          <input value={newKind} onChange={(e) => setNewKind(e.target.value)} placeholder="Kind (optional)" maxLength={64} />{" "}
          <input value={newIdent} onChange={(e) => setNewIdent(e.target.value)} placeholder="Identifier (optional)" maxLength={64} />{" "}
          <select value={newStatus} onChange={(e) => setNewStatus(e.target.value as api.EquipStatus)}>
            <option value="fmc">FMC</option>
            <option value="degraded">Degraded</option>
            <option value="down">Down</option>
          </select>{" "}
          <button type="submit" disabled={actionBusy} className="btn--secondary">Add unit</button>
        </form>
      )}
      {error && <p className="muted" style={{ color: "red" }}>{error}</p>}

      {equipment.length === 0 ? (
        loading ? (
          <div className="muted">Loading equipment…</div>
        ) : (
          <div className="dash-unavail">No active equipment.</div>
        )
      ) : (
        <>
          <div className="dash-grid">
            {equipment.map((eq) => {
              const statusClass = equipStatusPillClass(eq.status);
              return (
                <div
                  key={eq.id}
                  onClick={() => handleCardClick(eq)}
                  className="dash-card--click"
                  role="button"
                >
                  <div className="dash-card__head">
                    <h3 className="dash-card__title">{eq.name}</h3>
                    {eq.status && (
                      <span className={`dash-pill ${statusClass}`}>
                        {eq.status === "fmc" ? "FMC" : eq.status === "degraded" ? "Degraded" : "Down"}
                      </span>
                    )}
                  </div>
                  <div className="dash-card__sub">
                    {eq.kind ?? "Equipment"} • {eq.identifier ?? `#${eq.id}`}
                  </div>
                  {/* Location pill */}
                  <div className="dash-card__row">
                    <span className="dash-card__label">Current location:</span>
                    {" "}
                    {eq.location ? (
                      eq.location.label ?? (eq.location.job_id ? `Job ${eq.location.job_id}` : "Unknown")
                    ) : (
                      <span className="dash-unavail">Unavailable</span>
                    )}
                  </div>
                  {/* Latest inspection pill */}
                  <div className="dash-card__row">
                    <span className="dash-card__label">Latest inspection:</span>
                    {" "}
                    {eq.latest_inspection ? eq.latest_inspection.form_code : (
                      <span className="dash-unavail">None</span>
                    )}
                  </div>
                </div>
              );
            })}
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
