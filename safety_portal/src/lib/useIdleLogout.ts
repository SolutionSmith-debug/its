import { useEffect, useRef } from "react";

// Slice 8b (C10) — the SPA half of the admin 30-minute idle timeout. The SERVER is the
// boundary (it 401s an idle/captured admin cookie via the sliding window in requireSession);
// this hook is the UX layer for an ACTIVELY-USED admin session.
//
// Proactive idle-logout runs in BOTH modes: after IDLE_MS (30 min) of NO real input the admin is
// logged out, so a session is never held open without a human present. On real activity
// (mouse / key / click / scroll / touch) it pings /api/session (throttled) so requireSession
// re-issues + SLIDES the server cookie window — an admin reading + interacting never gets bounced.
//
// `paused` (a dirty form-editor draft is open) ADDS a wall-clock keep-alive: it pings /api/session
// every KEEPALIVE_MS even with NO input events (firing once immediately on activation), so a draft
// left in a BACKGROUNDED tab — where the browser throttles the activity timers and no events fire —
// keeps the server window sliding and the publish doesn't 401 when the admin returns. It is
// BOUNDED to the idle window: the keep-alive slides only WHILE real input is younger than IDLE_MS;
// once it goes stale the proactive logout above fires and the keep-alive stops, so an ABANDONED
// dirty tab still dies at ~30 min rather than living forever (audit 2026-06-09: dirty-editor
// unbounded-session). The draft is also localStorage-cached (draftCache), so an idle logout never
// loses the work.
//
// Mounted ONLY via AdminSessionGuard in App.tsx (rendered when role === "admin"), so it
// is admin-scoped by construction; submitters keep their 90-day session untouched.

const IDLE_MS = 30 * 60 * 1000;
const PING_THROTTLE_MS = 60 * 1000; // slide the server window at most once a minute (active use)
const CHECK_MS = 15 * 1000;
const KEEPALIVE_MS = 240 * 1000; // paused: wall-clock slide, comfortably inside the 30-min window

/** Fire-and-forget keep-alive: a single authenticated request slides the server cookie window. */
function pingSession(): void {
  fetch("/api/session", { credentials: "same-origin" }).catch(() => {});
}

export function useIdleLogout(onIdle: () => void, paused = false): void {
  const onIdleRef = useRef(onIdle);
  onIdleRef.current = onIdle;
  // Shared so the paused keep-alive can bound itself to the SAME idle window the proactive check
  // uses — once real input is stale beyond IDLE_MS, both stop and the session is allowed to die.
  const lastActivityRef = useRef(Date.now());

  // Proactive idle-logout + activity-driven keep-alive. Runs in BOTH modes (independent of
  // `paused`), so a dirty editor never buys an unbounded session.
  useEffect(() => {
    lastActivityRef.current = Date.now();
    let lastPing = 0;
    let firedIdle = false;

    const onActivity = () => {
      lastActivityRef.current = Date.now();
      firedIdle = false;
      // Throttled keep-alive: a single authenticated request slides the server window.
      if (Date.now() - lastPing > PING_THROTTLE_MS) {
        lastPing = Date.now();
        pingSession();
      }
    };

    const check = () => {
      if (!firedIdle && Date.now() - lastActivityRef.current >= IDLE_MS) {
        firedIdle = true;
        onIdleRef.current();
      }
    };

    const events = ["mousemove", "keydown", "click", "scroll", "touchstart"] as const;
    for (const e of events) window.addEventListener(e, onActivity, { passive: true });
    const timer = window.setInterval(check, CHECK_MS);

    return () => {
      for (const e of events) window.removeEventListener(e, onActivity);
      window.clearInterval(timer);
    };
  }, []);

  // PAUSED mode — a dirty editor is open. ADD a wall-clock keep-alive so a backgrounded tab (no
  // input events, throttled timers) keeps the server cookie window sliding and doesn't 401 when
  // the admin returns mid-edit. BOUNDED to the idle window: once real input is older than IDLE_MS
  // it stops sliding and the proactive logout above takes over, so an abandoned dirty tab dies.
  useEffect(() => {
    if (!paused) return;
    const keepAlive = () => {
      if (Date.now() - lastActivityRef.current < IDLE_MS) pingSession();
    };
    keepAlive(); // immediate slide on activation
    const timer = window.setInterval(keepAlive, KEEPALIVE_MS);
    return () => window.clearInterval(timer);
  }, [paused]);
}
