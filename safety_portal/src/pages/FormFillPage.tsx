import { useEffect, useMemo, useState } from "react";
import { AppHeader } from "../components/AppHeader";
import { useAuth } from "../lib/auth";
import * as api from "../lib/api";
import { formCatalog, getDefinition } from "../forms/registry";
import { FormRenderer, initialValues, type FormValues } from "../forms/FormRenderer";

function todayIso(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function FormFillPage({ onBack }: { onBack: () => void }) {
  const { logout } = useAuth();
  const catalog = useMemo(() => formCatalog(), []);

  const [jobs, setJobs] = useState<api.Job[]>([]);
  const [jobsErr, setJobsErr] = useState<string | null>(null);
  const [jobId, setJobId] = useState("");
  const [parentCode, setParentCode] = useState("");
  const [variantCode, setVariantCode] = useState("");
  const [workDate, setWorkDate] = useState(todayIso());

  const [values, setValues] = useState<FormValues>({});
  const [amendsUuid, setAmendsUuid] = useState<string | null>(null);
  const [prefillable, setPrefillable] = useState<api.RecentSubmission | null>(null);
  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.fetchJobs().then(setJobs).catch((e) => setJobsErr(e instanceof Error ? e.message : "load failed"));
  }, []);

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
    try {
      await api.submitForm({
        job_id: jobId,
        form_code: def.form_code,
        variant_label: def.variant_label,
        work_date: workDate,
        values,
        submission_uuid: crypto.randomUUID(),
        amends_uuid: amendsUuid,
      });
      setSubmitted(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Submission failed.");
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    setSubmitted(false);
    setParentCode("");
    setVariantCode("");
    setValues({});
    setAmendsUuid(null);
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
        <main className="page__main">
          <div className="card centered-card">
            <h1 className="page__heading">Submitted ✓</h1>
            <p className="muted">
              Your {def?.form_name} for {projectName ? `${projectName} on ` : ""}
              {workDate} was submitted. The office will confirm it once it’s filed.
            </p>
            <div className="jha__actions">
              <button className="btn btn--primary" onClick={reset}>Submit another</button>
              <button className="btn btn--secondary" onClick={onBack}>Home</button>
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
      <main className="page__main">
        <button className="btn btn--ghost btn--back" onClick={onBack}>← Home</button>

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
