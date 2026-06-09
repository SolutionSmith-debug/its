import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { AppHeader } from "../components/AppHeader";
import { useAuth } from "../lib/auth";
import * as api from "../lib/api";
import { formCatalog, getDefinition } from "../forms/registry";
import { FormRenderer, initialValues, type FormValues } from "../forms/FormRenderer";
import { useSubmissionId } from "./useSubmissionId";

function todayIso(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * The daily-form fill flow. Used two ways:
 *  - non-admin: <FormFillPage onBack={…} /> — HomePage ↔ form, with the back/Home
 *    buttons (behaviour unchanged from before the admin dashboard).
 *  - admin "Submit a form" tab: <FormFillPage tabBar={<AdminTabs …/>} /> — no onBack
 *    (the tab bar handles navigation), and the tab bar renders under the header.
 */
export function FormFillPage({ onBack, tabBar }: { onBack?: () => void; tabBar?: ReactNode }) {
  const { user, logout } = useAuth();
  const isAdmin = user?.role === "admin";
  const me = user?.username ?? "";
  const catalog = useMemo(() => formCatalog(), []);

  const [jobs, setJobs] = useState<api.Job[]>([]);
  const [jobsErr, setJobsErr] = useState<string | null>(null);
  const [jobId, setJobId] = useState("");
  const [parentCode, setParentCode] = useState("");
  const [variantCode, setVariantCode] = useState("");
  const [workDate, setWorkDate] = useState(todayIso());

  // Admin "filled out as" — the account this submission is attributed to (default =
  // self). Only admins ever see / send this; submitters always submit as themselves.
  // The list of accounts is fetched once when an admin opens the form. The server
  // re-validates the choice (role + target enabled), so this selector is convenience,
  // never the boundary.
  const [accounts, setAccounts] = useState<api.Account[]>([]);
  const [filledOutAs, setFilledOutAs] = useState("");

  const [values, setValues] = useState<FormValues>({});
  const [amendsUuid, setAmendsUuid] = useState<string | null>(null);
  const [prefillable, setPrefillable] = useState<api.RecentSubmission | null>(null);
  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [submittedAs, setSubmittedAs] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Stable across retries (lost-ACK idempotency); renewed only on a new submission (reset).
  const { submissionUuid, renew: renewSubmissionId } = useSubmissionId();

  useEffect(() => {
    api.fetchJobs().then(setJobs).catch((e) => setJobsErr(e instanceof Error ? e.message : "load failed"));
  }, []);

  // Admins only: load the account list once and default the attribution to self.
  // Submitters never call /api/admin/users (it would 403); they always submit as me.
  useEffect(() => {
    if (!isAdmin) return;
    setFilledOutAs(me);
    api.listAccounts().then(setAccounts).catch(() => setAccounts([]));
  }, [isAdmin, me]);

  const parent = catalog.find((p) => p.parent_form_code === parentCode) ?? null;
  const formCode = parent ? (parent.variants.length ? variantCode : (parent.form_code ?? "")) : "";
  const def = formCode ? getDefinition(formCode) : null;

  // (Re)initialize the fill state whenever the chosen form changes.
  useEffect(() => {
    const d = formCode ? getDefinition(formCode) : null;
    setValues(d ? initialValues(d) : {});
    setAmendsUuid(null);
  }, [formCode]);

  // Amend prefill: when job + form + work-date are all set, look for a prior submission.
  useEffect(() => {
    setPrefillable(null);
    if (jobId && formCode && workDate) {
      let active = true;
      api.fetchRecent(jobId, formCode, workDate).then((r) => {
        if (active) setPrefillable(r);
      }).catch(() => {});
      return () => {
        active = false;
      };
    }
  }, [jobId, formCode, workDate]);

  function loadAmend() {
    const d = formCode ? getDefinition(formCode) : null;
    if (!prefillable || !d) return;
    setValues({ ...initialValues(d), ...prefillable.values });
    setAmendsUuid(prefillable.submission_uuid);
    setPrefillable(null);
  }

  async function onSubmit() {
    if (!def || !jobId || !workDate) return;
    setBusy(true);
    setError(null);
    // Only an admin attributes to someone else; for a self-submit (or any submitter)
    // we omit submitted_as entirely so the server takes the normal self-submit path.
    const attributeTo = isAdmin && filledOutAs && filledOutAs !== me ? filledOutAs : undefined;
    try {
      await api.submitForm({
        job_id: jobId,
        form_code: def.form_code,
        variant_label: def.variant_label,
        work_date: workDate,
        values,
        submission_uuid: submissionUuid,
        amends_uuid: amendsUuid,
        submitted_as: attributeTo,
      });
      setSubmittedAs(attributeTo ?? null);
      setSubmitted(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Submission failed.");
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    setSubmitted(false);
    setSubmittedAs(null);
    setParentCode("");
    setVariantCode("");
    setValues({});
    setAmendsUuid(null);
    renewSubmissionId(); // a fresh id for the NEXT submission (the prior one succeeded)
    // Reset the attribution back to self for the next submission (admins only).
    if (isAdmin) setFilledOutAs(me);
  }

  if (submitted) {
    // Surface the job (not just the date) in the confirmation — a PM filing for
    // several jobs needs to see WHICH one was recorded. jobId/jobs are still in
    // scope (reset() clears the form, not the job), so the lookup resolves; the
    // fallback drops the clause if the job somehow isn't in the loaded list.
    const projectName = jobs.find((j) => j.job_id === jobId)?.project_name;
    return (
      <div className="page">
        <AppHeader title="Safety Portal" />
        {tabBar}
        <main className="page__main">
          <div className="card centered-card">
            <h1 className="page__heading">Submitted ✓</h1>
            <p className="muted">
              Your {def?.form_name} for {projectName ? `${projectName} on ` : ""}
              {workDate} was submitted. The office will confirm it once it’s filed.
            </p>
            {submittedAs ? (
              // Admin filled-out-as note — surfaces the attributed account so the admin
              // can confirm WHO it was recorded for (the true actor is still logged).
              <p className="muted">Submitted as <strong>{submittedAs}</strong>.</p>
            ) : null}
            <div className="jha__actions">
              <button className="btn btn--primary" onClick={reset}>Submit another</button>
              {onBack ? <button className="btn btn--secondary" onClick={onBack}>Home</button> : null}
            </div>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="page">
      <AppHeader
        title="New safety form"
        action={<button className="btn btn--ghost" onClick={() => void logout()}>Sign out</button>}
      />
      {tabBar}
      <main className="page__main">
        {onBack ? <button className="btn btn--ghost btn--back" onClick={onBack}>← Home</button> : null}

        <section className="card fr__select">
          <label className="field">
            <span className="field__label">Job *</span>
            <select className="field__input" value={jobId} onChange={(e) => setJobId(e.target.value)}>
              <option value="">Select a job…</option>
              {jobs.map((j) => <option key={j.job_id} value={j.job_id}>{j.project_name}</option>)}
            </select>
          </label>
          {jobsErr ? <p className="login__error" role="alert">{jobsErr}</p> : null}

          <label className="field">
            <span className="field__label">Form *</span>
            <select className="field__input" value={parentCode}
              onChange={(e) => { setParentCode(e.target.value); setVariantCode(""); }}>
              <option value="">Select a form…</option>
              {catalog.map((p) => <option key={p.parent_form_code} value={p.parent_form_code}>{p.name}</option>)}
            </select>
          </label>

          {parent && parent.variants.length ? (
            <label className="field">
              <span className="field__label">Type *</span>
              <select className="field__input" value={variantCode} onChange={(e) => setVariantCode(e.target.value)}>
                <option value="">Select a type…</option>
                {parent.variants.map((v) => <option key={v.form_code} value={v.form_code}>{v.variant_label}</option>)}
              </select>
            </label>
          ) : null}

          <label className="field">
            <span className="field__label">Work date *</span>
            <input className="field__input" type="date" value={workDate} onChange={(e) => setWorkDate(e.target.value)} />
          </label>

          {isAdmin ? (
            // Admin-only "Filled out as": attribute this submission to another account.
            // Default is the admin's own username. Submitters never see this (it isn't
            // rendered), and even if a forged value reached the server it is rejected
            // there (Invariant 2 — the selector is convenience, not the gate).
            <label className="field">
              <span className="field__label">Filled out as</span>
              <select
                className="field__input"
                value={filledOutAs}
                onChange={(e) => setFilledOutAs(e.target.value)}
              >
                <option value={me}>{me} (you)</option>
                {accounts
                  .filter((a) => a.username !== me)
                  .map((a) => (
                    <option key={a.username} value={a.username}>{a.username}</option>
                  ))}
              </select>
            </label>
          ) : null}
        </section>

        {prefillable ? (
          <div className="jha__notice" role="status">
            <strong>A submission already exists</strong> for this job, form, and date.{" "}
            <button className="btn btn--secondary" onClick={loadAmend}>Load & amend it</button>
          </div>
        ) : null}

        {def ? (
          <>
            {amendsUuid ? <p className="jha__notice"><strong>Amending</strong> a previous submission.</p> : null}
            <section className="card">
              <FormRenderer def={def} values={values} setValues={setValues} />
            </section>
            {error ? <p className="login__error" role="alert">{error}</p> : null}
            <div className="jha__actions">
              <button className="btn btn--primary btn--block" onClick={() => void onSubmit()} disabled={busy || !jobId}>
                {busy ? "Submitting…" : amendsUuid ? "Submit amendment" : "Submit"}
              </button>
            </div>
          </>
        ) : (
          <p className="muted">Pick a job and form to begin.</p>
        )}
      </main>
    </div>
  );
}
