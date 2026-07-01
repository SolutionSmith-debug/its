/**
 * Materials Catalog admin page (P3 Materials M1). List + create + per-row edit + soft-retire,
 * cap.materials.manage gating the write controls (the Worker re-gates). Mirrors the field-ops
 * write-UI test idiom: mock the lib + auth, resetAllMocks, default read-only, expect-inside-waitFor.
 */
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/fieldops_materials", () => ({
  fetchMaterials: vi.fn(),
  createMaterial: vi.fn(),
  updateMaterial: vi.fn(),
  retireMaterial: vi.fn(),
}));
vi.mock("../../lib/auth", () => ({ useAuth: vi.fn() }));

import * as api from "../../lib/fieldops_materials";
import { useAuth } from "../../lib/auth";
import { MaterialsCatalogPage } from "../MaterialsCatalogPage";

function authWith(capabilities: string[]) {
  return {
    user: capabilities.length ? { username: "u", role: "admin" as const, capabilities } : null,
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
  };
}

const ROWS: api.CatalogRow[] = [
  { id: 1, model_id: "MOD-1", manufacturer: "Acme", category: "module", key_specs: "500W", unit_cost: null, source_files: "[]", active: 1 },
  { id: 2, model_id: "INV-2", manufacturer: null, category: "inverter", key_specs: null, unit_cost: 1200, source_files: "[]", active: 1 },
];

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(useAuth).mockReturnValue(authWith([])); // default: logged-out shell
  vi.mocked(api.fetchMaterials).mockResolvedValue({ materials: ROWS, next_cursor: null });
});

describe("MaterialsCatalogPage", () => {
  it("renders the catalog; a read-only account (no manage cap) sees no write controls", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.materials.receive"]));
    const { queryByText } = render(<MaterialsCatalogPage onBack={() => {}} />);
    await waitFor(() => expect(queryByText("MOD-1")).not.toBeNull());
    expect(queryByText("+ Add a type")).toBeNull();
    expect(queryByText("Edit")).toBeNull();
    expect(queryByText("Retire")).toBeNull();
  });

  it("manager can add a type; reloads the list on success", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.materials.manage"]));
    vi.mocked(api.createMaterial).mockResolvedValue({ id: 99 });
    const { container, getByText } = render(<MaterialsCatalogPage onBack={() => {}} />);
    await waitFor(() => expect(api.fetchMaterials).toHaveBeenCalledTimes(1));
    fireEvent.click(getByText("+ Add a type"));
    const inputs = container.querySelectorAll("input.field__input");
    fireEvent.change(inputs[0], { target: { value: "NEW-MOD" } }); // model_id
    fireEvent.change(inputs[2], { target: { value: "module" } }); // category
    fireEvent.click(getByText("Add type"));
    await waitFor(() =>
      expect(api.createMaterial).toHaveBeenCalledWith(expect.objectContaining({ model_id: "NEW-MOD", category: "module" })),
    );
    await waitFor(() => expect(api.fetchMaterials).toHaveBeenCalledTimes(2)); // reload-after-write
  });

  it("manager can edit a row", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.materials.manage"]));
    vi.mocked(api.updateMaterial).mockResolvedValue(undefined);
    const { container, getAllByText, getByText } = render(<MaterialsCatalogPage onBack={() => {}} />);
    await waitFor(() => expect(getByText("MOD-1")).toBeTruthy());
    fireEvent.click(getAllByText("Edit")[0]);
    const inputs = container.querySelectorAll(".accounts__editor input.field__input");
    fireEvent.change(inputs[0], { target: { value: "MOD-1-EDIT" } });
    fireEvent.click(getByText("Save"));
    await waitFor(() => expect(api.updateMaterial).toHaveBeenCalledWith(1, expect.objectContaining({ model_id: "MOD-1-EDIT" })));
  });

  it("manager can soft-retire a row", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.materials.manage"]));
    vi.mocked(api.retireMaterial).mockResolvedValue(undefined);
    const { getAllByText } = render(<MaterialsCatalogPage onBack={() => {}} />);
    await waitFor(() => expect(getAllByText("Retire").length).toBeGreaterThan(0));
    fireEvent.click(getAllByText("Retire")[0]);
    await waitFor(() => expect(api.retireMaterial).toHaveBeenCalledWith(1));
  });
});
