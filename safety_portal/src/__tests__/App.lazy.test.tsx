/**
 * Admin route-splitting smoke (optimization #8): FormsPage / AccountsPage / MaterialsCatalogPage
 * are React.lazy chunks behind App's Suspense boundary. This smokes ONE of them end-to-end:
 * an admin opens the Materials Catalog home card and the lazily-imported page mounts — the REAL
 * page module through the REAL dynamic import (only its data lib is mocked), proving the lazy
 * wiring + Suspense fallback path. Field-critical views stay eager as static imports (enforced
 * by the imports at the top of App.tsx itself, not asserted here).
 */
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/auth", () => ({ useAuth: vi.fn() }));
vi.mock("../lib/fieldops_materials", () => ({
  fetchMaterials: vi.fn(async () => ({ materials: [], next_cursor: null })),
  createMaterial: vi.fn(),
  updateMaterial: vi.fn(),
  retireMaterial: vi.fn(),
}));

import { App } from "../App";
import { useAuth } from "../lib/auth";

afterEach(cleanup);
beforeEach(() => {
  window.history.replaceState(null, "", "/"); // G2.5: each test cold-starts at home
  vi.mocked(useAuth).mockReturnValue({
    user: { username: "boss", role: "admin" as const, capabilities: ["cap.materials.manage"] },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
  });
});

describe("App — lazy admin chunks (Suspense-gated view switch)", () => {
  it("the Materials Catalog card mounts the lazily-split page through the Suspense boundary", async () => {
    const { getByText, findByText } = render(<App />);
    fireEvent.click(getByText("Materials Catalog")); // the capability-gated home card
    // The chunk resolves through the real dynamic import; the page's own intro proves the mount.
    expect(await findByText(/material type vocabulary/)).toBeTruthy();
  });

  it("a COLD-LOADED /materials deep link (G2.5) mounts the same lazy chunk — routes keep the split", async () => {
    window.history.replaceState(null, "", "/materials");
    const { findByText } = render(<App />);
    // Same real dynamic import, now reached straight from the URL — no home-card click.
    expect(await findByText(/material type vocabulary/)).toBeTruthy();
  });
});
