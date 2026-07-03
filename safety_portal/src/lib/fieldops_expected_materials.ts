// Expected-materials API client (Material receipts M1). Same-origin cookie fetch; no auth header.
// READ + receive/flag-incident gated cap.materials.receive (per-job ownership-scoped for
// non-admins); expectation CRUD gated cap.materials.manage (the Worker re-gates every call).
// receive/flagIncident ship here for completeness — M2 wires them into the daily form's
// deliveries region ("Confirm receipt" / "Report material incident →").

export type ExpectedMaterialStatus = "expected" | "received" | "incident";

export interface ExpectedMaterialRow {
  id: number;
  material_id: number | null; // catalog-picked rows; null = free-text
  material_name: string | null; // resolved catalog model_id (display; null for free-text rows)
  description: string | null;
  qty: number | null;
  unit: string | null;
  expected_date: string | null; // YYYY-MM-DD
  status: ExpectedMaterialStatus;
  received_at: number | null; // epoch seconds, stamped by receive/flag-incident
  received_by_name: string | null; // DISPLAY NAME ONLY (W9) — null when the account has no roster link
  qty_received: number | null;
  note: string | null;
  seq: number;
}

export async function fetchExpectedMaterials(jobId: string): Promise<{ expected_materials: ExpectedMaterialRow[] }> {
  const res = await fetch(`/api/fieldops/expected-materials?job_id=${encodeURIComponent(jobId)}`, {
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error("Could not load this job's expected materials.");
  return ((await res.json()) as { expected_materials: ExpectedMaterialRow[] }) ?? { expected_materials: [] };
}

async function postJson<T = { ok: boolean }>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(err.error ?? `Request failed (${res.status})`);
  }
  return (await res.json()) as T;
}

/** Content fields for add/edit. Catalog-pick rows set material_id (description optional extra);
 *  free-text rows omit material_id and REQUIRE description (the Worker 400s otherwise). */
export interface ExpectedMaterialFields {
  material_id?: number;
  description?: string;
  qty?: number;
  unit?: string;
  expected_date?: string;
}

export async function createExpectedMaterial(
  jobId: string,
  fields: ExpectedMaterialFields & { seq?: number },
): Promise<{ id: number }> {
  return postJson<{ ok: boolean; id: number }>("/api/fieldops/expected-material", { job_id: jobId, ...fields });
}

/** Full-replace edit of the content fields. Only status='expected' rows are editable —
 *  a received/incident row is a receipt record (the Worker 409s not_editable). */
export async function updateExpectedMaterial(id: number, fields: ExpectedMaterialFields): Promise<void> {
  await postJson(`/api/fieldops/expected-material/${id}/update`, fields);
}

/** Reorder: seq-only write, allowed on any active row (the checklist planRenumber convention). */
export async function setExpectedMaterialSeq(id: number, seq: number): Promise<void> {
  await postJson(`/api/fieldops/expected-material/${id}/seq`, { seq });
}

/** Deactivate (soft-delete; idempotent — history kept). */
export async function deactivateExpectedMaterial(id: number): Promise<void> {
  await postJson(`/api/fieldops/expected-material/${id}/delete`, {});
}

/** Confirm receipt (expected → received). Repeat → the Worker 409s already_actioned. */
export async function receiveExpectedMaterial(
  id: number,
  opts: { qty_received?: number; note?: string } = {},
): Promise<void> {
  await postJson(`/api/fieldops/expected-material/${id}/receive`, opts);
}

/** Flag a delivery problem (expected → incident). note is REQUIRED (the Worker 400s without it). */
export async function flagExpectedMaterialIncident(
  id: number,
  note: string,
  qtyReceived?: number,
): Promise<void> {
  await postJson(`/api/fieldops/expected-material/${id}/flag-incident`, {
    note,
    ...(qtyReceived !== undefined ? { qty_received: qtyReceived } : {}),
  });
}
