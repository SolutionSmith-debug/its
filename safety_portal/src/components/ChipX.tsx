import { useEffect, useState } from "react";

/**
 * ChipX (R7) — the small ✕ remove button that lives inside a .dash-chip / CC row, hardened for
 * touch: the visual stays chip-sized (~¼ scale) while global.css gives .chip-x a ::before overlay
 * expanding the EFFECTIVE hit area to ~40px, and a destructive tap is TWO-STEP — the first tap
 * arms an inline "Remove?" state (auto-disarms after a few seconds), the second
 * confirms. ConfirmDelete (ChecklistItemForm) stays the pattern for full-size destructive rows;
 * this is its chip-context sibling — same two-step semantics, chip-scale rendering.
 *
 * aria contract: unarmed = `ariaLabel`; armed = `Confirm ${ariaLabel}` (mirrors ConfirmDelete's
 * Confirm labelling so tests/AT address the second step explicitly).
 */
export function ChipX({
  ariaLabel,
  disabled,
  onConfirm,
}: {
  ariaLabel: string;
  disabled?: boolean;
  onConfirm: () => void;
}) {
  const [armed, setArmed] = useState(false);

  // Auto-disarm: an armed chip left alone reverts — a stray first tap must not leave a live
  // one-tap destroyer sitting in the UI.
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 5000);
    return () => clearTimeout(t);
  }, [armed]);

  if (!armed) {
    return (
      <button
        type="button"
        className="chip-x"
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => setArmed(true)}
      >
        ✕
      </button>
    );
  }
  return (
    <button
      type="button"
      className="chip-x chip-x--armed"
      aria-label={`Confirm ${ariaLabel}`}
      disabled={disabled}
      onClick={() => {
        setArmed(false);
        onConfirm();
      }}
    >
      Remove?
    </button>
  );
}
