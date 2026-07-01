import { useState, useEffect } from "react";
import type { FormEvent } from "react";
import * as api from "../lib/fieldops_equipment";
import { PageShell } from "../components/PageShell";
import { equipStatusLabel, equipStatusPillClass, type EquipmentListRow } from "./equipmentView";

type Notify = { ok: boolean; text: string };

/**
 * Admin equipment ROSTER manager (cap.equipment.manage) — add a unit, edit its details, or retire
 * it. Reached from the "Manage equipment" button on the dashboard. Mirrors the URS-Marine
 * EquipmentManager refinement. The dashboard already gates the entry button on cap.equipment.manage
 * (a CONVENIENCE gate — the Worker re-gates every create/update/retire call server-side, Invariant 2).
 * Readiness STATUS is set from each unit's detail view (the field-action path), not here.
 */
export function EquipmentManageView({ onBack, onHome }: { onBack: () => void; onHome: () => void }) {
  const [roster, setRoster] = useState<EquipmentListRow[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<Notify | null>(null);

  // Add-a-unit form
  const [newName, setNewName] = useState("");
  const [newKind, setNewKind] = useState("");
  const [newIdent, setNewIdent] = useState("");
  const [newStatus, setNewStatus] = useState<api.EquipStatus>("fmc");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .fetchEquipmentList(undefined)
      .then((data) => {
        if (!live) return;
        setRoster(data.equipment);
        setCursor(data.next_cursor);
      })
      .catch(() => live && setError("Failed to load equipment."))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, []);

  async function reloadRoster() {
    const data = await api.fetchEquipmentList(undefined);
    setRoster(data.equipment);
    setCursor(data.next_cursor);
  }

  async function loadMore() {
    if (!cursor) return;
    try {
      const data = await api.fetchEquipmentList(cursor);
      setRoster((prev) => [...prev, ...data.equipment]);
      setCursor(data.next_cursor);
    } catch {
      setError("Failed to load more equipment.");
    }
  }

  async function submitCreate(e: FormEvent) {
    e.preventDefault();
    if (busy || newName.trim() === "") return;
    setBusy(true);
    setActionMsg(null);
    try {
      await api.createEquipment({
        name: newName.trim(),
        kind: newKind.trim() || undefined,
        identifier: newIdent.trim() || undefined,
        status: newStatus,
      });
      setNewName("");
      setNewKind("");
      setNewIdent("");
      setNewStatus("fmc");
      await reloadRoster();
      setActionMsg({ ok: true, text: "Equipment added." });
    } catch (err) {
      setActionMsg({ ok: false, text: err instanceof Error ? err.message : "Add failed." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageShell onHome={onHome}>
      <div className="dash-back-btn">
        <button onClick={onBack} className="btn--secondary">
          ← Back to equipment
        </button>
      </div>

      <h2 className="page__heading">Manage equipment</h2>
      <p className="dash__intro">
        Add a unit, edit its details, or retire it from the active roster. Retiring keeps the unit's
        history. Readiness status is set from each unit's detail view.
      </p>

      {actionMsg && (
        <div className={`banner ${actionMsg.ok ? "banner--ok" : "banner--err"}`}>{actionMsg.text}</div>
      )}
      {error && <div className="banner banner--err">{error}</div>}

      <section className="card dash-section">
        <h3 className="dash-detail__h2">Add equipment</h3>
        <form onSubmit={submitCreate} className="dash-row" aria-label="Add equipment">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="New unit name"
            maxLength={128}
          />{" "}
          <input
            value={newKind}
            onChange={(e) => setNewKind(e.target.value)}
            placeholder="Kind (optional)"
            maxLength={64}
          />{" "}
          <input
            value={newIdent}
            onChange={(e) => setNewIdent(e.target.value)}
            placeholder="Identifier (optional)"
            maxLength={64}
          />{" "}
          <label className="dash-card__label">
            Readiness:{" "}
            <select value={newStatus} onChange={(e) => setNewStatus(e.target.value as api.EquipStatus)}>
              <option value="fmc">FMC</option>
              <option value="degraded">Degraded</option>
              <option value="down">Down</option>
            </select>
          </label>{" "}
          <button type="submit" disabled={busy || newName.trim() === ""} className="btn--primary">
            Add unit
          </button>
        </form>
      </section>

      {loading ? (
        <p className="muted">Loading…</p>
      ) : roster.length === 0 ? (
        <div className="dash-empty">No equipment yet — add the first unit above.</div>
      ) : (
        <>
          <div className="dash-grid">
            {roster.map((u) => (
              <UnitEditor key={u.id} unit={u} onChanged={reloadRoster} onNotify={setActionMsg} />
            ))}
          </div>
          {cursor && (
            <div className="dash-row dash-load-more">
              <button onClick={loadMore} className="btn--secondary">
                Load more
              </button>
            </div>
          )}
        </>
      )}
    </PageShell>
  );
}

/** One roster unit — editable name/kind/identifier + retire. Local controlled state (re-seeded when
 *  the parent reload remounts the row on the changed id set). */
function UnitEditor({
  unit,
  onChanged,
  onNotify,
}: {
  unit: EquipmentListRow;
  onChanged: () => Promise<void> | void;
  onNotify: (m: Notify) => void;
}) {
  const [editOpen, setEditOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState(unit.name);
  const [kind, setKind] = useState(unit.kind ?? "");
  const [ident, setIdent] = useState(unit.identifier ?? "");

  async function submitEdit(e: FormEvent) {
    e.preventDefault();
    if (busy || name.trim() === "") return;
    setBusy(true);
    try {
      await api.updateEquipment(unit.id, {
        name: name.trim(),
        kind: kind.trim() || undefined,
        identifier: ident.trim() || undefined,
      });
      setEditOpen(false);
      onNotify({ ok: true, text: "Equipment updated." });
      await onChanged();
    } catch (err) {
      onNotify({ ok: false, text: err instanceof Error ? err.message : "Update failed." });
      setBusy(false);
    }
  }

  async function submitRetire() {
    if (busy) return;
    setBusy(true);
    try {
      await api.retireEquipment(unit.id);
      onNotify({ ok: true, text: "Equipment retired." });
      await onChanged();
    } catch (err) {
      onNotify({ ok: false, text: err instanceof Error ? err.message : "Retire failed." });
      setBusy(false);
    }
  }

  return (
    <section className="card equip-manage-card">
      <div className="dash-card__head">
        <h3 className="dash-card__title">{unit.name}</h3>
        <span className={equipStatusPillClass(unit.status)}>{equipStatusLabel(unit.status)}</span>
      </div>
      <div className="dash-card__sub">
        {unit.kind ?? "Equipment"} · {unit.identifier ?? `#${unit.id}`}
      </div>

      {editOpen ? (
        <form onSubmit={submitEdit} aria-label={`Edit ${unit.name}`}>
          <div className="dash-row">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" maxLength={128} />{" "}
            <input value={kind} onChange={(e) => setKind(e.target.value)} placeholder="Kind (optional)" maxLength={64} />{" "}
            <input
              value={ident}
              onChange={(e) => setIdent(e.target.value)}
              placeholder="Identifier (optional)"
              maxLength={64}
            />
          </div>
          <div className="dash-row">
            <button type="submit" disabled={busy} className="btn--edit">
              Save
            </button>{" "}
            <button type="button" onClick={() => setEditOpen(false)} className="btn--secondary">
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className="dash-row">
          <button type="button" onClick={() => setEditOpen(true)} className="btn--edit">
            Edit details
          </button>{" "}
          <button type="button" onClick={submitRetire} disabled={busy} className="btn--retire">
            Retire unit
          </button>
        </div>
      )}
    </section>
  );
}
