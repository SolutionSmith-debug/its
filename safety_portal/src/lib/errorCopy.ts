// R1 — the shared error-copy foundation: every worker error code the field-ops feature can return,
// mapped to plain-language copy, plus the ApiError the lib fetch helpers throw.
//
// CONTRACT: pages branch on `err.code` (the raw wire code, e.g. 'below_target'), NEVER on
// `err.message` — the message is HUMAN COPY (this map) and may be reworded freely. The raw code +
// HTTP status are preserved on the ApiError (and echoed to console.warn by raiseApiError) so
// diagnosis never loses the wire truth. Unknown codes get a humanized generic fallback — no more
// raw "Request failed (409)" banners.
//
// The map consolidates every hand-rolled page translation that predated it (below_target,
// not_placed, already_assigned, login_not_allowed) — add new copy HERE, not in pages.

export const ERROR_COPY: Record<string, string> = {
  // ── auth / session / gates ─────────────────────────────────────────────────────────────────────
  unauthenticated: "You're not signed in — log in and try again.",
  bad_session: "Your session has expired — log in again.",
  invalid_credentials: "Incorrect username or password.",
  forbidden: "You don't have permission to do that.",

  // ── generic request shape ──────────────────────────────────────────────────────────────────────
  bad_request: "The request couldn't be read — please try again.",
  not_found: "That item no longer exists — refresh and try again.",
  internal_error: "Something went wrong on the server — please try again.",

  // ── tasks ──────────────────────────────────────────────────────────────────────────────────────
  forbidden_task: "You can only update tasks assigned to you.",
  forbidden_target: "Managers can only assign tasks to subcontractor accounts.",
  invalid_description: "Enter a task description (up to 256 characters).",
  invalid_status: "That isn't a valid task status.",
  invalid_personnel_id: "Pick a valid person.",
  not_active: "That job is closed — work can't be added to it.",

  // ── checklists (templates / items / instances) ─────────────────────────────────────────────────
  invalid_item_type: "That isn't a valid checklist item type.",
  invalid_label: "Enter an item label (up to 256 characters).",
  invalid_seq: "The item's position must be a non-negative whole number.",
  form_code_required: "Form-linked items need a form code.",
  unknown_form_code: "That form code doesn't match any form in the catalog — pick one from the form list.",
  invalid_target_count: "The target must be a whole number of at least 1.",
  invalid_title: "Enter a checklist title (up to 256 characters).",
  invalid_active: "Active must be on or off.",
  no_default_template: "The daily checklist template hasn't been set up yet.",
  invalid_template_id: "Pick a valid checklist.",
  invalid_assignee: "Pick a valid person to assign to.",
  invalid_due_date: "Enter the due date as YYYY-MM-DD.",
  template_not_found: "That checklist no longer exists — refresh the list.",
  assignee_not_found: "That person isn't on the active roster.",
  already_assigned: "That checklist is already assigned for this job and date.",
  empty_template: "This checklist has no items yet — add at least one before assigning it.",
  job_and_date_required:
    "This checklist contains form-linked items — pick both a job and a due date so filings can check them off.",
  invalid_note: "The note is too long (up to 2000 characters).",
  invalid_photo_ref: "The photo reference isn't valid.",
  invalid_value_num: "Enter a non-negative number.",
  below_target: "The value you recorded is below the target — the item stays open.",
  note_required: "Add a note explaining the shortfall to complete this item below target.",
  auto_close_only: "This item completes automatically when the linked form is filed — it can't be checked by hand.",
  no_instance: "There's no daily checklist for you today.",
  not_complete: "Finish the remaining checklist items first.",

  // ── time entries ───────────────────────────────────────────────────────────────────────────────
  invalid_hours: "Hours must be a number greater than 0 and at most 24.",
  invalid_uuid: "The entry couldn't be identified — please retry.",
  invalid_amends_uuid: "The entry being corrected couldn't be identified.",
  invalid_notes: "The notes are too long (up to 2000 characters).",
  invalid_submitted_as: "That isn't a valid account to submit as.",
  unknown_attributed_user: "That account doesn't exist or is disabled.",
  forbidden_personnel: "You can only log time for yourself or crew you added.",
  unknown_task: "That task doesn't belong to this job.",
  uuid_conflict: "This entry was already saved.",

  // ── jobs / personnel / crew (shared field-ops vocabulary) ──────────────────────────────────────
  invalid_job_id: "Pick a valid job.",
  unknown_job: "That job doesn't exist or is closed.",
  job_exists: "A job with that ID already exists.",
  unknown_personnel: "That person isn't on the active roster.",
  unknown_client: "That client doesn't exist.",
  unknown_account: "That account doesn't exist.",
  invalid_id: "That reference isn't valid — refresh and try again.",
  invalid_name: "Enter a name (up to 128 characters).",
  invalid_trade: "The trade is too long (up to 64 characters).",
  invalid_username: "Usernames are first.last (letters, numbers, dots).",
  invalid_password: "The password doesn't meet the requirements.",
  invalid_role: "That isn't a valid role.",
  invalid_project_name: "Enter a project name.",
  invalid_progress: "Progress must be a whole number from 0 to 100.",
  invalid_lifecycle: "That isn't a valid job state.",
  invalid_address: "The address isn't valid.",
  invalid_email: "Enter a valid email address.",
  invalid_phone: "The phone number isn't valid.",
  invalid_cc: "CC lists take up to 5 valid email addresses.",
  invalid_contact_name: "The contact name isn't valid.",
  invalid_client_name: "Enter a client name.",
  invalid_client_id: "Pick a valid client.",
  invalid_client_field: "One of the client fields isn't valid.",
  client_id_and_new_client: "Pick an existing client OR enter a new one — not both.",
  counter_unavailable: "A job number couldn't be issued — please try again.",
  exists: "That already exists.",
  username_already_linked: "That account is already linked to another person.",
  not_placed: "You must be placed on a job first. Ask your crew lead or the office to place you.",
  login_not_allowed: "Crew added here are field-only (no login).",

  // ── equipment / materials / rollup (the remaining field-ops write vocabulary) ──────────────────
  invalid_kind: "That isn't a valid equipment kind.",
  invalid_identifier: "The identifier is too long.",
  invalid_status_note: "The status note is too long.",
  invalid_log_type: "That isn't a valid log type.",
  invalid_detail: "The detail text isn't valid.",
  invalid_window: "That isn't a valid reporting window.",
  invalid_source_files: "The source-file list isn't valid.",
};

/** Humanize an unknown wire code: 'some_new_code' → 'some new code'. */
function humanizeCode(code: string): string {
  return code.replace(/_/g, " ");
}

/**
 * Translate a worker error code (+ HTTP status) into plain-language copy. Every mapped code gets its
 * copy; an unmapped code gets a humanized generic; no code at all falls back on the status class.
 */
export function errorText(code: string | null | undefined, status?: number): string {
  if (code && ERROR_COPY[code]) return ERROR_COPY[code];
  if (code) return `Something went wrong (${humanizeCode(code)}). Please try again.`;
  if (status === 401) return ERROR_COPY.bad_session;
  if (status === 403) return ERROR_COPY.forbidden;
  if (status === 404) return ERROR_COPY.not_found;
  if (status !== undefined && status >= 500) return ERROR_COPY.internal_error;
  return "Something went wrong. Please try again.";
}

/**
 * The error the lib fetch helpers throw on a non-OK response. `message` is human copy (errorText);
 * `code` is the raw wire code for page-level branching; `status` is the HTTP status.
 */
export class ApiError extends Error {
  readonly code: string | null;
  readonly status: number;

  constructor(code: string | null, status: number) {
    super(errorText(code, status));
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

/**
 * Shared non-OK handler for the lib fetch helpers: extract the worker's `{ error }` code (tolerant
 * of an empty/non-JSON body), preserve the raw code in console.warn, and throw the ApiError carrying
 * human copy + code + status.
 */
export async function raiseApiError(res: Response): Promise<never> {
  let code: string | null = null;
  try {
    const body = (await res.json()) as { error?: unknown };
    if (typeof body?.error === "string") code = body.error;
  } catch {
    /* non-JSON body → status-based copy */
  }
  console.warn(`API error ${res.status}${code ? `: ${code}` : ""} (${res.url})`);
  throw new ApiError(code, res.status);
}
