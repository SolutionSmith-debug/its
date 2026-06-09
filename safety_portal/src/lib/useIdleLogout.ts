import { useEffect, useRef } from "react";

// Slice 8b (C10) — the SPA half of the admin 5-minute idle timeout. The SERVER is the
// boundary (it 401s an idle/captured admin cookie via the sliding window in requireSession);
// this hook is the UX layer for an ACTIVELY-USED admin session:
//   - on real activity (mouse / key / click / scroll / touch) it pings /api/session
//     (throttled) so requireSession re-issues + SLIDES the server cookie window — an admin
//     reading + interacting never gets bounced;
//   - after IDLE_MS with NO activity it logs the admin out proactively (instead of waiting
//     for the next request to 401), returning them to the login screen.
// Used only inside the admin shell (App.tsx routes admins to AdminApp), so it is
// admin-scoped by construction; submitters keep their 90-day session untouched.

const IDLE_MS = 5 * 60 * 1000;
const PING_THROTTLE_MS = 60 * 1000; // slide the server window at most once a minute
const CHECK_MS = 15 * 1000;

export function useIdleLogout(onIdle: () => void): void {
  const onIdleRef = useRef(onIdle);
  onIdleRef.current = onIdle;

  useEffect(() => {
    let lastActivity = Date.now();
    let lastPing = 0;
    let firedIdle = false;

    const onActivity = () => {
      lastActivity = Date.now();
      firedIdle = false;
      // Throttled keep-alive: a single authenticated request slides the server window.
      if (Date.now() - lastPing > PING_THROTTLE_MS) {
        lastPing = Date.now();
        fetch("/api/session", { credentials: "same-origin" }).catch(() => {});
      }
    };

    const check = () => {
      if (!firedIdle && Date.now() - lastActivity >= IDLE_MS) {
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
}
