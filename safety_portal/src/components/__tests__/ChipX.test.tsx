/**
 * ChipX (R7) — the two-step chip-scale remove button: first tap ARMS ("Remove?"), second tap
 * confirms; a stray arm auto-disarms after a beat. The visual stays ~¼ button size; the enlarged
 * hit area is CSS (::before overlay in global.css) and isn't assertable in jsdom.
 */
import { cleanup, fireEvent, render, act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChipX } from "../ChipX";

afterEach(cleanup);

describe("ChipX", () => {
  it("first tap arms (no onConfirm), second tap confirms exactly once", () => {
    const onConfirm = vi.fn();
    const { getByLabelText } = render(<ChipX ariaLabel="Remove Thing" onConfirm={onConfirm} />);
    const x = getByLabelText("Remove Thing");
    expect(x.textContent).toBe("✕");
    fireEvent.click(x); // arm
    expect(onConfirm).not.toHaveBeenCalled();
    const confirm = getByLabelText("Confirm Remove Thing");
    expect(confirm.textContent).toBe("Remove?");
    fireEvent.click(confirm); // confirm
    expect(onConfirm).toHaveBeenCalledTimes(1);
    // Back to the unarmed state after confirming.
    expect(getByLabelText("Remove Thing").textContent).toBe("✕");
  });

  it("an armed chip auto-disarms after the timeout (a stray tap never leaves a live destroyer)", () => {
    vi.useFakeTimers();
    try {
      const onConfirm = vi.fn();
      const { getByLabelText, queryByLabelText } = render(
        <ChipX ariaLabel="Remove Thing" onConfirm={onConfirm} />,
      );
      fireEvent.click(getByLabelText("Remove Thing")); // arm
      expect(queryByLabelText("Confirm Remove Thing")).not.toBeNull();
      act(() => {
        vi.advanceTimersByTime(5001);
      });
      expect(queryByLabelText("Confirm Remove Thing")).toBeNull(); // disarmed
      expect(onConfirm).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("disabled blocks both arming and confirming", () => {
    const onConfirm = vi.fn();
    const { getByLabelText } = render(<ChipX ariaLabel="Remove Thing" disabled onConfirm={onConfirm} />);
    fireEvent.click(getByLabelText("Remove Thing"));
    expect(onConfirm).not.toHaveBeenCalled();
    // Still unarmed — a disabled button ignores the click entirely.
    expect(getByLabelText("Remove Thing").textContent).toBe("✕");
  });
});
