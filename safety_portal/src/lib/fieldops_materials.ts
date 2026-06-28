// Material catalog API client (P3 Materials M1). Same-origin cookie fetch; no auth header.
// READ gated cap.materials.receive; WRITE gated cap.materials.manage (the Worker re-gates).

export interface CatalogRow {
  id: number;
  model_id: string;
  manufacturer: string | null;
  category: string;
  key_specs: string | null;
  unit_cost: number | null;
  source_files: string | null; // JSON array string (provenance)
  active: number;
}

export interface MaterialsListResponse {
  materials: CatalogRow[];
  next_cursor: string | null;
}

export async function fetchMaterials(opts?: { cursor?: string; all?: boolean }): Promise<MaterialsListResponse> {
  const q = new URLSearchParams();
  if (opts?.cursor) q.set("cursor", opts.cursor);
  if (opts?.all) q.set("all", "1");
  const res = await fetch(`/api/fieldops/materials?${q.toString()}`, { credentials: "same-origin" });
  if (!res.ok) throw new Error("Could not load the material catalog.");
  return ((await res.json()) as MaterialsListResponse) ?? { materials: [], next_cursor: null };
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

export interface MaterialFields {
  model_id: string;
  manufacturer?: string;
  category: string;
  key_specs?: string;
  unit_cost?: number;
}

export async function createMaterial(body: MaterialFields): Promise<{ id: number }> {
  return postJson<{ ok: boolean; id: number }>("/api/fieldops/material", body);
}
export async function updateMaterial(id: number, body: MaterialFields): Promise<void> {
  await postJson(`/api/fieldops/material/${id}/update`, body);
}
export async function retireMaterial(id: number): Promise<void> {
  await postJson(`/api/fieldops/material/${id}/delete`, {});
}
