/**
 * Materials Catalog admin page (P3 Materials M1). List + create + per-row edit + soft-retire,
 * cap.materials.manage gating the write controls (the Worker re-gates), grouped by category with a
 * chip filter bar. Mirrors the field-ops write-UI test idiom: mock the lib + auth, resetAllMocks,
 * default read-only, expect-inside-waitFor. Row-mutation tests scope queries to a specific row's
 * `.card` via `within()` rather than `getAllByText(...)[0]`, since grouping reorders rows in the DOM
 * (alphabetical-by-category, not fetch order) — scoping keeps the tests independent of that order.
 */
import { cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
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
    const { container, getByText } = render(<MaterialsCatalogPage onBack={() => {}} />);
    await waitFor(() => expect(getByText("MOD-1")).toBeTruthy());
    const card = getByText("MOD-1").closest(".card") as HTMLElement;
    fireEvent.click(within(card).getByText("Edit"));
    const inputs = container.querySelectorAll(".accounts__editor input.field__input");
    fireEvent.change(inputs[0], { target: { value: "MOD-1-EDIT" } });
    fireEvent.click(getByText("Save"));
    await waitFor(() => expect(api.updateMaterial).toHaveBeenCalledWith(1, expect.objectContaining({ model_id: "MOD-1-EDIT" })));
  });

  it("manager can soft-retire a row", async () => {
    vi.mocked(useAuth).mockReturnValue(authWith(["cap.materials.manage"]));
    vi.mocked(api.retireMaterial).mockResolvedValue(undefined);
    const { getByText } = render(<MaterialsCatalogPage onBack={() => {}} />);
    await waitFor(() => expect(getByText("MOD-1")).toBeTruthy());
    const card = getByText("MOD-1").closest(".card") as HTMLElement;
    fireEvent.click(within(card).getByText("Retire"));
    await waitFor(() => expect(api.retireMaterial).toHaveBeenCalledWith(1));
  });

  describe("category grouping", () => {
    it("groups rows into a section per category with a count pill, sorted alphabetically", async () => {
      vi.mocked(useAuth).mockReturnValue(authWith(["cap.materials.receive"]));
      const { getAllByRole, getByText } = render(<MaterialsCatalogPage onBack={() => {}} />);
      await waitFor(() => expect(getByText("MOD-1")).toBeTruthy());

      const sections = getAllByRole("region"); // one <section aria-label="<cat> material types"> per category
      const labels = sections.map((s) => s.getAttribute("aria-label"));
      expect(labels).toEqual(["inverter material types", "module material types"]); // alphabetical: inverter < module

      // "inverter" section (1 row) comes before "module" section (1 row) in DOM order.
      const invSection = sections.find((s) => s.getAttribute("aria-label") === "inverter material types")!;
      expect(within(invSection).getByText("INV-2")).toBeTruthy();
      expect(within(invSection).getByText("1")).toBeTruthy(); // count pill
    });

    it("buckets a blank/whitespace category under Uncategorized, sorted last", async () => {
      vi.mocked(useAuth).mockReturnValue(authWith(["cap.materials.receive"]));
      vi.mocked(api.fetchMaterials).mockResolvedValue({
        materials: [
          ...ROWS,
          { id: 3, model_id: "MYST-3", manufacturer: null, category: "   ", key_specs: null, unit_cost: null, source_files: "[]", active: 1 },
        ],
        next_cursor: null,
      });
      const { getAllByRole, getByText } = render(<MaterialsCatalogPage onBack={() => {}} />);
      await waitFor(() => expect(getByText("MYST-3")).toBeTruthy());

      const sections = getAllByRole("region");
      const labels = sections.map((s) => s.getAttribute("aria-label"));
      expect(labels).toEqual(["inverter material types", "module material types", "Uncategorized material types"]);
    });

    it("the category chip bar filters the view down to one category; the same chip toggles back to All", async () => {
      vi.mocked(useAuth).mockReturnValue(authWith(["cap.materials.receive"]));
      const { getAllByRole, getByText, queryByText } = render(<MaterialsCatalogPage onBack={() => {}} />);
      await waitFor(() => expect(getByText("MOD-1")).toBeTruthy());

      fireEvent.click(getByText("module", { selector: ".mats-cat-filter__chip" }));
      expect(getByText("MOD-1")).toBeTruthy();
      expect(queryByText("INV-2")).toBeNull();
      expect(getAllByRole("region").map((s) => s.getAttribute("aria-label"))).toEqual(["module material types"]);

      fireEvent.click(getByText("module", { selector: ".mats-cat-filter__chip" })); // toggle off → back to All
      await waitFor(() => expect(getByText("INV-2")).toBeTruthy());
    });

    it("does not render the filter bar when every row shares one category", async () => {
      vi.mocked(useAuth).mockReturnValue(authWith(["cap.materials.receive"]));
      vi.mocked(api.fetchMaterials).mockResolvedValue({
        materials: [ROWS[0], { ...ROWS[1], category: "module" }],
        next_cursor: null,
      });
      const { getByText, queryByRole } = render(<MaterialsCatalogPage onBack={() => {}} />);
      await waitFor(() => expect(getByText("MOD-1")).toBeTruthy());
      expect(queryByRole("group", { name: "Filter by category" })).toBeNull();
    });
  });
});
