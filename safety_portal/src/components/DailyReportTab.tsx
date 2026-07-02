import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import * as api from "../lib/api";
import * as jobs from "../lib/fieldops_jobtracker";
import { fetchDailyFormStatus, type DailyFormStatus } from "../lib/fieldops_daily_form";
import { formCatalog, getDefinition, resolveFormTarget } from "../forms/registry";
import { FormRenderer, initialValues, type FormLinkAdapter, type FormValues } from "../forms/FormRenderer";
import { useSubmissionId } from "../pages/useSubmissionId";
import type { FormPrefill } from "../pages/FormFillPage";
import { useAuth } from "../lib/auth";
import {
  ROSTER_LINK_COPY,
  SectionError,
  SectionLoading,
  SectionRefreshWarn,
  errMsg,
  fmtDate,
  pacificToday,
} from "./myTasksShared";

// ─────────────────────────────────────────────────────────────────────────────
// SOP daily form (slice D2) — the Daily tab IS the form now.
//
// Replaces DailyChecklistSection (the R2 checkbox checklist): the daily SOP content lives in the
// daily-report-v2 FORM DEFINITION (guidance + form_link sections, slice D1), rendered INLINE here
// through the SAME machinery the generic fill page uses — FormRenderer + initialValues (the shared
// rendering contract), useSubmissionId (lost-ACK idempotency), api.submitForm (the send-free
// /api/submit path) and api.fetchRecent (the amend-prefill lookup). What is deliberately NOT reused
// is FormFillPage's page shell (job/form pickers, admin submit-as, the Submitted receipt screen):
// the tab has a FIXED envelope (job from the actor's placement, date from the selector below) and
// stays in place after filing, so it carries its own thin state machine instead.
//
// Placement: the actor's job comes from the Job Tracker viewer data (fetchJobList's
// viewer_current_job — the manager holds cap.jobtracker.read), NOT /checklist/mine (retired for
// daily; the checklist engine still serves assigned inspections). The R1/R2 empty-state reasons are
// re-derived: role ≠ manager (session) → not-a-manager copy; parent-reported linked:false
// (/tasks/mine) → the roster-link copy; placed nowhere → the not-placed copy.
//
// Prefill (best-effort, NEVER a blocker): the job detail (cap.jobtracker.read) seeds
// crew_progress rows from the placed crew, equipment_on_site rows from equipment-on-site, and
// prepared_by from the viewer's roster name. A detail failure → empty tables + a soft warn.
//
// form_link deep-links ride the R3 openForm machinery (App captures the originating view; the
// submitted form returns here), and the live "Filed ✓ <time> by <name>" indicators + the
// "already filed" banner come from GET /api/fieldops/daily-form/status (family-matched
// server-side; display-name-only attribution — the W9 posture).
//
// Never-silent (the R2 bar): distinct loading / error+Retry for placement; soft warns for a failed
// status read or prefill; inline submit errors with the button re-enabled.
// ─────────────────────────────────────────────────────────────────────────────

/** The daily-report PARENT family (catalog.json) — the tab renders its CURRENT version. */
const DAILY_REPORT_PARENT = "daily-report";

const NOT_MANAGER_COPY =
  "The daily report is for crew-lead managers who are placed on a job — it doesn't apply to this account.";
const NOT_PLACED_COPY =
  "You're not placed on a job yet — ask the office to place you. Your daily report appears once you're placed.";

/** What the tab reports up after each placement load (drives the parent's auto-tab-switch +
 *  Add-crew placement hint + Log-time deep-link — the same duties the old checklist instance had). */
export interface DailyPlacement {
  job_id: string;
  project_name: string | null;
}

/** Epoch seconds → localized short time (the filed-indicator timestamp). */
function fmtEpochTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function DailyReportTab({
  linked,
  onOpenForm,
  refreshToken = 0,
  onLoaded,
}: {
  /** /tasks/mine `linked` from the parent (null while that fetch is in flight) — disambiguates
   *  "no placement" into unlinked-account vs not-placed copy without an extra fetch. */
  linked: boolean | null;
  /** R3 deep-link opener (App.openForm) — absent renders the form_link buttons disabled. */
  onOpenForm?: (p: FormPrefill) => void;
  /** Bump to refetch placement + filed status (page Refresh / focus — the parent owns the trigger). */
  refreshToken?: number;
  /** Reports each placement load up ({ placement: null } for a non-manager / unplaced actor). */
  onLoaded?: (info: { placement: DailyPlacement | null }) => void;
}) {
  const { user } = useAuth();
  const isManager = user?.role === "manager";

  // ── The form definition: the daily-report parent's CURRENT version (v2 today; robust to a v3). ──
  // Resolved once — the catalog + definitions are build-time bundles.
  const [def] = useState(() => {
    const parent = formCatalog().find((p) => p.parent_form_code === DAILY_REPORT_PARENT);
    return parent?.form_code ? getDefinition(parent.form_code) : null;
  });

  const [placement, setPlacement] = useState<DailyPlacement | null>(null);
  const [placementLoading, setPlacementLoading] = useState(true);
  const [placementError, setPlacementError] = useState<string | null>(null);
  const [prefillWarn, setPrefillWarn] = useState<string | null>(null);

  const [date, setDate] = useState(pacificToday());
  const dateRef = useRef(date);
  dateRef.current = date;
  const [values, setValues] = useState<FormValues>(() => (def ? initialValues(def) : {}));
  const [amendsUuid, setAmendsUuid] = useState<string | null>(null);
  const [prefillable, setPrefillable] = useState<api.RecentSubmission | null>(null);

  const [status, setStatus] = useState<DailyFormStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  const [busy, setBusy] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [justFiled, setJustFiled] = useState(false);
  // Stable across retries (lost-ACK idempotency, same hook as the fill page); renewed on success.
  const { submissionUuid, renew: renewSubmissionId } = useSubmissionId();

  // Ref'd so effects don't re-fire when the parent re-renders with a new inline callback.
  const onLoadedRef = useRef(onLoaded);
  onLoadedRef.current = onLoaded;
  // The last successfully built prefill (crew/equipment/prepared_by) — re-applied on the
  // post-submit reset so "file another" starts from the same seeded tables.
  const lastPrefill = useRef<FormValues>({});
  // Any field touched since load/submit (a ref — read inside the prefill effect without re-firing
  // it): a detail-arriving prefill must never clobber typed work.
  const dirtyRef = useRef(false);

  // Field edits go through this wrapper so the dirty flag tracks real typing only.
  const editValues: Dispatch<SetStateAction<FormValues>> = (v) => {
    dirtyRef.current = true;
    setValues(v);
  };

  // ── Draft persistence (D2 regression BLOCK fix) ─────────────────────────────────────────────────
  // A form_link deep-link navigates away and UNMOUNTS this tab (App swaps its single page node), so
  // pure component state = the manager's whole typed day silently lost on "Create Incident Report".
  // Drafts persist per (job, date) in sessionStorage on every edit, restore on mount / date switch
  // (winning over prefill), and clear on successful submit. Storage failures are non-fatal (quota /
  // private mode) — worst case reverts to pre-fix behavior.
  const readDraft = (job: string, d: string): FormValues | null => {
    try {
      const raw = sessionStorage.getItem(`its-daily-draft:${job}:${d}`);
      return raw ? (JSON.parse(raw) as FormValues) : null;
    } catch {
      return null;
    }
  };
  const clearDraft = (job: string, d: string) => {
    try {
      sessionStorage.removeItem(`its-daily-draft:${job}:${d}`);
    } catch {
      /* non-fatal */
    }
  };
  useEffect(() => {
    if (!placement || !dirtyRef.current) return;
    try {
      sessionStorage.setItem(`its-daily-draft:${placement.job_id}:${date}`, JSON.stringify(values));
    } catch {
      /* non-fatal */
    }
  }, [values, placement, date]);

  // ── Placement (the envelope job) — Job Tracker viewer data. ────────────────────────────────────
  async function loadPlacement() {
    setPlacementLoading(true);
    setPlacementError(null);
    try {
      const list = await jobs.fetchJobList("active");
      const jobId = list.viewer_current_job ?? null;
      if (!jobId) {
        setPlacement(null);
        onLoadedRef.current?.({ placement: null });
        return;
      }
      const p: DailyPlacement = {
        job_id: jobId,
        // Best-effort from the list page; the detail fetch below fills a miss (job beyond page 1).
        project_name: list.jobs.find((j) => j.job_id === jobId)?.project_name ?? null,
      };
      setPlacement(p);
      onLoadedRef.current?.({ placement: p });
    } catch (err) {
      setPlacementError(errMsg(err, "Could not load your job placement."));
    } finally {
      setPlacementLoading(false);
    }
  }

  useEffect(() => {
    if (!isManager) {
      // Not a manager → no placement, by definition. Report up so the parent's auto-switch settles.
      setPlacementLoading(false);
      onLoadedRef.current?.({ placement: null });
      return;
    }
    void loadPlacement();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshToken, isManager]);

  // ── Best-effort prefill from the job detail (crew / equipment / prepared_by). ──────────────────
  const placedJob = placement?.job_id ?? null;
  useEffect(() => {
    if (!placedJob || !def) return;
    let active = true;
    (async () => {
      try {
        const d = await jobs.fetchJobDetail(placedJob);
        if (!active) return;
        setPrefillWarn(null);
        // Fill a project-name miss from the detail header (and report the enriched placement up).
        setPlacement((p) => {
          if (!p || p.job_id !== placedJob || p.project_name !== null) return p;
          const enriched = { ...p, project_name: d.job.project_name };
          onLoadedRef.current?.({ placement: enriched });
          return enriched;
        });
        const seeded: FormValues = {};
        if (d.viewer_personnel?.name) seeded.prepared_by = d.viewer_personnel.name;
        if (d.job.crew.length > 0) {
          seeded.crew_progress = d.job.crew.map((m) => ({
            crew_subcontractor: m.trade ? `${m.name} (${m.trade})` : m.name,
            manpower: "",
            todays_progress: "",
          }));
        }
        if (d.job.equipment_on_site.length > 0) {
          seeded.equipment_on_site = d.job.equipment_on_site.map((e) => ({
            equipment_type: e.identifier ? `${e.name} (${e.identifier})` : e.name,
            owner_rental: "",
          }));
        }
        lastPrefill.current = seeded;
        // A persisted draft (typed work from before an unmount) WINS over the prefill; otherwise
        // apply the seed only over a pristine form — never clobber typed work on a refresh.
        const draft = readDraft(placedJob, dateRef.current);
        if (draft) {
          dirtyRef.current = true;
          setValues({ ...initialValues(def), ...draft });
        } else if (!dirtyRef.current) setValues({ ...initialValues(def), ...seeded });
      } catch {
        // Best-effort by design (spec): failure = empty tables + a soft warn, never a blocker.
        if (active) {
          setPrefillWarn(
            "Couldn't prefill crew and equipment from the Job Tracker — the tables start blank. Refresh to retry.",
          );
        }
      }
    })();
    return () => {
      active = false;
    };
  }, [placedJob, def, refreshToken]);

  // ── Filed status (the form_link indicators + the filed banner) + the amend candidate. ──────────
  async function loadStatus(jobId: string, forDate: string) {
    try {
      const s = await fetchDailyFormStatus(jobId, forDate);
      setStatus(s);
      setStatusError(null);
    } catch (err) {
      // The form stays fillable — the indicators are additive. But never silent (R2 bar).
      setStatusError(errMsg(err, "Couldn't check what's already been filed."));
    }
  }

  useEffect(() => {
    if (!placedJob) return;
    let active = true;
    void (async () => {
      // Wrapped so a stale (job/date-switched) response can't land: loadStatus sets state directly
      // on the manual-retry path, but the effect path re-checks `active` via this shim.
      try {
        const s = await fetchDailyFormStatus(placedJob, date);
        if (!active) return;
        setStatus(s);
        setStatusError(null);
      } catch (err) {
        if (active) setStatusError(errMsg(err, "Couldn't check what's already been filed."));
      }
    })();
    // The amend candidate (the existing fill-page machinery: fetchRecent → "Load & amend it").
    setPrefillable(null);
    if (def) {
      api
        .fetchRecent(placedJob, def.form_code, date)
        .then((r) => {
          if (active) setPrefillable(r);
        })
        .catch(() => {});
    }
    return () => {
      active = false;
    };
  }, [placedJob, date, def, refreshToken]);

  // ── The R2-built empty / loading / error states (mutually exclusive). ───────────────────────────
  if (!isManager) {
    return (
      <section className="card dash-section" aria-label="Daily report status">
        <h3 className="dash-detail__h2">Daily report</h3>
        <div className="dash-unavail">{NOT_MANAGER_COPY}</div>
      </section>
    );
  }
  if (!def) {
    // A packaging regression (the daily-report definition missing from the bundle) must fail LOUD.
    return (
      <section className="card dash-section" aria-label="Daily report status">
        <h3 className="dash-detail__h2">Daily report</h3>
        <div className="banner banner--err" role="alert">
          The Daily Report form definition failed to load — tell the office. This is a portal build
          problem, not something you can fix in the field.
        </div>
      </section>
    );
  }
  if (placementLoading && !placement) return <SectionLoading label="Loading your daily report…" />;
  if (placementError && !placement) {
    return <SectionError message={placementError} onRetry={() => void loadPlacement()} what="loading your daily report" />;
  }
  if (!placement) {
    return (
      <section className="card dash-section" aria-label="Daily report status">
        <h3 className="dash-detail__h2">Daily report</h3>
        <div className="dash-unavail">{linked === false ? ROSTER_LINK_COPY : NOT_PLACED_COPY}</div>
      </section>
    );
  }

  const today = pacificToday();
  const isToday = date === today;
  const filedForDate = status?.daily_filed ?? null;
  const validDate = /^\d{4}-\d{2}-\d{2}$/.test(date);

  function onDateChange(next: string) {
    // max= guards the picker UI; this guards typed input. Empty/future → clamp to today.
    const clamped = /^\d{4}-\d{2}-\d{2}$/.test(next) && next <= pacificToday() ? next : pacificToday();
    setDate(clamped);
    // A date switch starts a NEW submission for that date. Typed work stays under ITS OWN date's
    // draft (the save effect already persisted it); the new date shows its own draft or the seed.
    const draft = placement && def ? readDraft(placement.job_id, clamped) : null;
    if (draft && def) {
      dirtyRef.current = true;
      setValues({ ...initialValues(def), ...draft });
    } else if (def) {
      dirtyRef.current = false;
      setValues({ ...initialValues(def), ...lastPrefill.current });
    }
    setAmendsUuid(null);
    setJustFiled(false);
    setSubmitError(null);
  }

  function loadAmend() {
    if (!prefillable || !def) return;
    dirtyRef.current = true; // amend-loaded values are the manager's work — persist as a draft
    setValues({ ...initialValues(def), ...prefillable.values });
    setAmendsUuid(prefillable.submission_uuid);
    dirtyRef.current = true; // loaded content counts as work-in-progress (prefill must not clobber it)
    setPrefillable(null);
  }

  async function onSubmit() {
    if (!def || !placement || !validDate || busy) return;
    setBusy(true);
    setSubmitError(null);
    setJustFiled(false);
    try {
      // The standard send-free submit path — the same api.submitForm the fill page uses. No
      // submitted_as: the Daily tab is the manager's own surface (self-submit only).
      await api.submitForm({
        job_id: placement.job_id,
        form_code: def.form_code,
        variant_label: def.variant_label,
        work_date: date,
        values,
        submission_uuid: submissionUuid,
        amends_uuid: amendsUuid,
      });
      setJustFiled(true);
      dirtyRef.current = false;
      clearDraft(placement.job_id, date);
      setAmendsUuid(null);
      renewSubmissionId(); // fresh id for the NEXT submission (this one succeeded)
      // Reset to a fresh seeded form for a potential file-another/amend pass.
      setValues({ ...initialValues(def), ...lastPrefill.current });
      // Reflect the filing in the indicators + banner (best-effort; failure soft-warns), and
      // refresh the amend candidate so "Load & amend it" now offers the just-filed submission.
      await loadStatus(placement.job_id, date);
      api
        .fetchRecent(placement.job_id, def.form_code, date)
        .then((r) => setPrefillable(r))
        .catch(() => {});
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "Submission failed.");
    } finally {
      setBusy(false);
    }
  }

  // R3 deep-link adapter for the definition's form_link sections. `open` rides App.openForm (which
  // captures My Tasks as the return target); the indicator label comes from the status read.
  const formLinks: FormLinkAdapter | undefined = onOpenForm
    ? {
        open: (parentFormCode: string) => {
          const target = resolveFormTarget(parentFormCode);
          onOpenForm({
            jobId: placement.job_id,
            parentCode: target.parentCode,
            variantCode: target.variantCode || undefined,
            workDate: date,
          });
        },
        filedLabel: (parentFormCode: string) => {
          const entry = status?.filed[parentFormCode];
          if (!entry) return null;
          return `Filed ✓ ${fmtEpochTime(entry.filed_at)}${entry.filed_by_name ? ` by ${entry.filed_by_name}` : ""}`;
        },
      }
    : undefined;

  const formCard = (
    <>
      {amendsUuid ? (
        <p className="jha__notice">
          <strong>Amending</strong> the submission filed for {fmtDate(date)}.
        </p>
      ) : null}
      <section className="card">
        <FormRenderer def={def} values={values} setValues={editValues} formLinks={formLinks} />
      </section>
      {submitError ? (
        <p className="login__error" role="alert">
          {submitError}
        </p>
      ) : null}
      <div className="jha__actions">
        <button
          type="button"
          className="btn btn--primary btn--block"
          aria-label="Submit daily report"
          onClick={() => void onSubmit()}
          disabled={busy || !validDate}
        >
          {busy ? "Submitting…" : amendsUuid ? "Submit amendment" : "Submit daily report"}
        </button>
      </div>
    </>
  );

  return (
    <section className="dash-section" aria-label="Daily report">
      <div className="card dash-section">
        <h3 className="dash-detail__h2">
          Daily report
          <span className="dash-card__sub">
            {" "}
            · {placement.project_name ?? placement.job_id} · {placement.job_id}
          </span>
        </h3>
        <label className="field">
          <span className="field__label">Report date</span>
          <input
            className="field__input"
            type="date"
            aria-label="Report date"
            value={date}
            max={today}
            onChange={(e) => onDateChange(e.target.value)}
          />
        </label>

        {statusError && (
          <SectionRefreshWarn
            message={statusError}
            onRetry={() => void loadStatus(placement.job_id, date)}
            what="checking filed forms"
          />
        )}
        {prefillWarn && <div className="dash-unavail" role="status">{prefillWarn}</div>}

        {justFiled && (
          <div className="banner banner--ok" role="status">
            Submitted ✓ — the office will confirm it once it’s filed.
          </div>
        )}
        {filedForDate && (
          <div className="banner banner--ok" role="status" aria-label="Daily report filed">
            Daily report filed ✓ {fmtEpochTime(filedForDate.filed_at)}
            {filedForDate.filed_by_name ? ` by ${filedForDate.filed_by_name}` : ""} for {fmtDate(date)}.
            {prefillable ? (
              <>
                {" "}
                <button type="button" className="btn btn--secondary" onClick={loadAmend}>
                  Load &amp; amend it
                </button>
              </>
            ) : null}
          </div>
        )}
      </div>

      {/* Past dates default to the filed state first: the form collapses behind a disclosure.
          Today's form (and any unfiled past date) renders open. */}
      {!isToday && filedForDate ? (
        <details className="dash-completed">
          <summary className="dash-card__sub" style={{ cursor: "pointer" }}>
            File another or amend for this date
          </summary>
          {formCard}
        </details>
      ) : (
        formCard
      )}
    </section>
  );
}
