import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useIdleLogout } from "../useIdleLogout";

// Slice 8b (C10) — useIdleLogout. The SERVER cookie window is the real boundary; this is the SPA
// UX layer. Proactive logout runs in BOTH modes after IDLE_MS (30 min) of NO real input.
//   NORMAL (paused=false): activity-driven slide only; no wall-clock keep-alive.
//   PAUSED  (paused=true, a dirty editor open): ADDS a wall-clock keep-alive that pings
//     /api/session immediately + every KEEPALIVE_MS (4 min) so a backgrounded dirty draft keeps
//     the server window sliding and never 401s mid-edit — BUT it is bounded to the idle window, so
//     an ABANDONED dirty tab (no real input for 30 min) still logs out and the keep-alive stops.
// The real browser supplies window/fetch/timers; here we fake the clock and stub fetch.

const IDLE_MS = 30 * 60 * 1000;
const KEEPALIVE_MS = 240 * 1000;
const CHECK_MS = 15 * 1000;

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.useFakeTimers();
  fetchMock = vi.fn(() => Promise.resolve());
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** How many keep-alive slides (GET /api/session) have fired so far. */
const sessionPings = () =>
  fetchMock.mock.calls.filter((args) => String(args[0]) === "/api/session").length;

describe("useIdleLogout — NORMAL mode (paused=false)", () => {
  it("logs out proactively after the 30-minute idle window with no activity", () => {
    const onIdle = vi.fn();
    renderHook(() => useIdleLogout(onIdle, false));
    // Just shy of the window — still considered active.
    vi.advanceTimersByTime(IDLE_MS - CHECK_MS);
    expect(onIdle).not.toHaveBeenCalled();
    // Cross the window — the 15s check fires the proactive logout exactly once.
    vi.advanceTimersByTime(CHECK_MS * 2);
    expect(onIdle).toHaveBeenCalledTimes(1);
  });

  it("does NOT run the wall-clock keep-alive — pings are activity-driven, never on a timer", () => {
    renderHook(() => useIdleLogout(vi.fn(), false));
    vi.advanceTimersByTime(KEEPALIVE_MS * 3);
    expect(sessionPings()).toBe(0);
  });
});

describe("useIdleLogout — PAUSED mode (paused=true, dirty editor open)", () => {
  it("slides the server window: pings immediately on activation, then every 4 minutes", () => {
    renderHook(() => useIdleLogout(vi.fn(), true));
    expect(sessionPings()).toBe(1); // immediate slide on activation
    vi.advanceTimersByTime(KEEPALIVE_MS);
    expect(sessionPings()).toBe(2);
    vi.advanceTimersByTime(KEEPALIVE_MS);
    expect(sessionPings()).toBe(3);
  });

  it("is BOUNDED: an abandoned dirty editor still logs out at ~30 min, and the keep-alive stops", () => {
    const onIdle = vi.fn();
    renderHook(() => useIdleLogout(onIdle, true));
    // No real input — the keep-alive slides for a while, but the proactive logout still fires.
    vi.advanceTimersByTime(IDLE_MS + CHECK_MS);
    expect(onIdle).toHaveBeenCalledTimes(1);
    // Past the window the keep-alive stops sliding a dead session (no further pings).
    const pingsAtIdle = sessionPings();
    vi.advanceTimersByTime(KEEPALIVE_MS * 3);
    expect(sessionPings()).toBe(pingsAtIdle);
  });

  it("does NOT bounce an admin who keeps editing — real input resets the idle window", () => {
    const onIdle = vi.fn();
    renderHook(() => useIdleLogout(onIdle, true));
    // Periodic real input just inside the window — never goes idle.
    for (let i = 0; i < 4; i++) {
      vi.advanceTimersByTime(IDLE_MS - CHECK_MS);
      window.dispatchEvent(new Event("keydown"));
    }
    vi.advanceTimersByTime(CHECK_MS * 2);
    expect(onIdle).not.toHaveBeenCalled();
  });
});

describe("useIdleLogout — mode transition", () => {
  it("stops the keep-alive when the editor closes (paused true → false)", () => {
    const { rerender } = renderHook(({ paused }) => useIdleLogout(vi.fn(), paused), {
      initialProps: { paused: true },
    });
    vi.advanceTimersByTime(KEEPALIVE_MS); // immediate + one interval slide
    const pingsWhilePaused = sessionPings();
    expect(pingsWhilePaused).toBeGreaterThanOrEqual(2);
    // Editor closes → the wall-clock keep-alive is torn down; no further timer pings.
    rerender({ paused: false });
    vi.advanceTimersByTime(KEEPALIVE_MS * 3);
    expect(sessionPings()).toBe(pingsWhilePaused);
  });
});
