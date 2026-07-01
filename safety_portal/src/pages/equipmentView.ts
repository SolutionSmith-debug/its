// Shared display helpers for the Field-Ops Equipment multi-view (Dashboard → Detail → Manage).
// Mirrors the URS-Marine refinement's lib/jobStatus.ts + lib/format.ts split so the three views
// never drift on how a readiness status renders. Consumes the portal's OWN response shapes
// (fieldops_equipment.ts) — no URS lib/api port.

import type { EquipmentHeader, EquipmentListResponse } from "../lib/fieldops_equipment";

/** One dashboard-list row (header + its point-in-time location / latest inspection / recent logs). */
export type EquipmentListRow = EquipmentListResponse["equipment"][number];

/** epoch SECONDS (as stored in D1) → ×1000 for a locale-aware JS Date; null → em-dash. */
export function fmtDateTime(epochSeconds: number | null): string {
  if (!epochSeconds) return "—";
  return new Date(epochSeconds * 1000).toLocaleString();
}

/** Fixed-precision number; null/NaN → em-dash. */
export function fmtNumber(val: number | null, digits = 2): string {
  if (val == null || isNaN(val)) return "—";
  return val.toFixed(digits);
}

/** Equipment readiness → human label (the mission-capability wording, shared with the URS fork). */
export function equipStatusLabel(status: EquipmentHeader["status"]): string {
  if (status === "fmc") return "Full Mission Capable";
  if (status === "degraded") return "Degraded";
  if (status === "down") return "Down";
  return status;
}

/** Equipment readiness → FULL pill class (`dash-pill` + variant): fmc = green (ok),
 *  degraded = gold (warn), down = red (danger), unknown → muted. */
export function equipStatusPillClass(status: EquipmentHeader["status"]): string {
  if (status === "fmc") return "dash-pill dash-pill--ok";
  if (status === "degraded") return "dash-pill dash-pill--warn";
  if (status === "down") return "dash-pill dash-pill--danger";
  return "dash-pill";
}
