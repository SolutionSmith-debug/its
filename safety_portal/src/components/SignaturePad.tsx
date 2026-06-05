import { useCallback, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

interface SignaturePadProps {
  width?: number;
  height?: number;
  strokeWidth?: number;
  /** Fires on each stroke end and on clear. svgPath = combined SVG path `d`; empty = no signature. */
  onChange?: (svgPath: string, isEmpty: boolean) => void;
}

/**
 * On-screen signature capture that emits TRUE SVG path data (vector), not raster.
 * The mission (§7) requires SVG path data; canvas libs (signature_pad,
 * react-signature-canvas) export raster PNG and do not satisfy it. Hand-rolled
 * over Pointer Events so it owns the exact `d` string and works on phones/tablets.
 *
 * Mobile correctness: touch-action:none on the surface (in CSS) + setPointerCapture
 * + getCoalescedEvents for smooth, unbroken strokes outdoors.
 */
export function SignaturePad({
  width = 600,
  height = 180,
  strokeWidth = 2.5,
  onChange,
}: SignaturePadProps) {
  const [paths, setPaths] = useState<string[]>([]); // each completed stroke = one `d`
  const currentRef = useRef<string>(""); // in-progress `d`
  const drawingRef = useRef(false);
  const svgRef = useRef<SVGSVGElement>(null);
  const [, force] = useState(0);

  const toLocal = useCallback(
    (clientX: number, clientY: number) => {
      const svg = svgRef.current;
      if (!svg) return { x: 0, y: 0 };
      const r = svg.getBoundingClientRect();
      const x = ((clientX - r.left) / r.width) * width;
      const y = ((clientY - r.top) / r.height) * height;
      return { x: Math.round(x * 100) / 100, y: Math.round(y * 100) / 100 };
    },
    [width, height],
  );

  const onPointerDown = useCallback(
    (e: ReactPointerEvent<SVGSVGElement>) => {
      e.preventDefault();
      // Keep receiving move/up if the finger/stylus drifts outside the surface.
      // setPointerCapture can throw (detached element / released pointer) — never
      // let that abort the stroke.
      try {
        // currentTarget (the <svg> the listeners live on) — deterministic even if a
        // new stroke starts atop an already-drawn <path>.
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch {
        /* non-fatal */
      }
      drawingRef.current = true;
      const { x, y } = toLocal(e.clientX, e.clientY);
      currentRef.current = `M ${x} ${y}`;
      force((n) => n + 1);
    },
    [toLocal],
  );

  const onPointerMove = useCallback(
    (e: ReactPointerEvent<SVGSVGElement>) => {
      if (!drawingRef.current) return;
      e.preventDefault();
      const native = e.nativeEvent;
      // getCoalescedEvents() yields the high-frequency points batched into one move
      // (smooth strokes on fast devices). It can legitimately return [] — fall back
      // to the event itself so no point is ever dropped.
      const coalesced =
        typeof native.getCoalescedEvents === "function" ? native.getCoalescedEvents() : [];
      const events = coalesced.length > 0 ? coalesced : [native];
      let d = currentRef.current;
      for (const pe of events) {
        const { x, y } = toLocal(pe.clientX, pe.clientY);
        d += ` L ${x} ${y}`;
      }
      currentRef.current = d;
      force((n) => n + 1);
    },
    [toLocal],
  );

  const endStroke = useCallback(
    (e: ReactPointerEvent<SVGSVGElement>) => {
      if (!drawingRef.current) return;
      e.preventDefault();
      drawingRef.current = false;
      const finished = currentRef.current;
      currentRef.current = "";
      if (finished.includes("L")) {
        // ignore stray taps with no movement
        setPaths((prev) => {
          const next = [...prev, finished];
          onChange?.(next.join(" "), false);
          return next;
        });
      }
      force((n) => n + 1);
    },
    [onChange],
  );

  const clear = useCallback(() => {
    setPaths([]);
    currentRef.current = "";
    drawingRef.current = false;
    onChange?.("", true);
    force((n) => n + 1);
  }, [onChange]);

  const combined = [...paths, currentRef.current].filter(Boolean).join(" ");
  const isEmpty = combined.length === 0;

  return (
    <div className="sig">
      <svg
        ref={svgRef}
        className="sig__surface"
        viewBox={`0 0 ${width} ${height}`}
        style={{ aspectRatio: `${width} / ${height}` }}
        role="img"
        aria-label="Signature capture area"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endStroke}
        onPointerCancel={endStroke}
        onPointerLeave={endStroke}
      >
        <path
          d={combined}
          fill="none"
          stroke="#0e0e0e"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <div className="sig__bar">
        <span className="sig__hint">
          {isEmpty ? "Sign above with finger or stylus" : "Captured as SVG vector"}
        </span>
        <button type="button" className="btn btn--secondary" onClick={clear} disabled={isEmpty}>
          Clear
        </button>
      </div>
    </div>
  );
}
