/**
 * submitForm — R3-F2 photo-payload 413 fix (Complete-State Slice 2).
 *
 * Locks: (1) the client pre-payload check mirrors the Worker's exact measurement
 * (JSON.stringify(values).length vs PAYLOAD_MAX = 1_800_000, worker/index.ts:521,:604) and blocks
 * BEFORE any network call with actionable copy; (2) the Worker's machine reasons — 413 `too_large`,
 * 400 `invalid_photo` (+ its `detail` reason from validatePhotoValues) — map to actionable
 * ERROR_COPY instead of the old dead-end "Please try again."; (3) unknown_job + the generic
 * fallback keep their existing copy.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SUBMIT_PAYLOAD_MAX, type SubmitBody, submitForm } from "../api";
import { ERROR_COPY } from "../errorCopy";

const fetchMock = vi.fn();

function body(values: Record<string, unknown>): SubmitBody {
  return {
    job_id: "JOB-A",
    form_code: "daily-report-v5",
    variant_label: null,
    work_date: "2026-07-03",
    values,
    submission_uuid: "uuid-1",
  };
}

function reply(status: number, payload: unknown): Response {
  return { ok: status < 400, status, json: async () => payload } as unknown as Response;
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
});
afterEach(() => vi.unstubAllGlobals());

describe("submitForm — client pre-payload check (R3-F2)", () => {
  it("an over-cap values payload blocks BEFORE any network call, with the too_large copy", async () => {
    // JSON.stringify wraps the string in quotes: repeat(SUBMIT_PAYLOAD_MAX) stringifies to
    // SUBMIT_PAYLOAD_MAX + 2 chars inside the object — strictly over the cap.
    const oversized = { photos: "x".repeat(SUBMIT_PAYLOAD_MAX) };
    await expect(submitForm(body(oversized))).rejects.toThrow(ERROR_COPY.too_large);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("a payload under the cap goes to the network (the check measures exactly what the Worker does)", async () => {
    fetchMock.mockResolvedValue(reply(200, { ok: true }));
    await expect(submitForm(body({ weather: "Sunny" }))).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("submitForm — actionable Worker error copy", () => {
  it("413 too_large maps to the remove-a-photo copy (a bare retry can never succeed)", async () => {
    fetchMock.mockResolvedValue(reply(413, { error: "too_large" }));
    await expect(submitForm(body({}))).rejects.toThrow(ERROR_COPY.too_large);
  });

  it("400 invalid_photo prefers the field-actionable detail copy when mapped", async () => {
    fetchMock.mockResolvedValue(reply(400, { error: "invalid_photo", detail: "photo_too_large" }));
    await expect(submitForm(body({}))).rejects.toThrow(ERROR_COPY.photo_too_large);
    fetchMock.mockResolvedValue(reply(400, { error: "invalid_photo", detail: "too_many_photos" }));
    await expect(submitForm(body({}))).rejects.toThrow(ERROR_COPY.too_many_photos);
  });

  it("400 invalid_photo with a MAPPED detail prefers the detail copy", async () => {
    fetchMock.mockResolvedValue(reply(400, { error: "invalid_photo", detail: "mixed_photo_array" }));
    await expect(submitForm(body({}))).rejects.toThrow(ERROR_COPY.mixed_photo_array);
  });

  it("400 invalid_photo with an unmapped detail falls back to the invalid_photo copy", async () => {
    fetchMock.mockResolvedValue(reply(400, { error: "invalid_photo", detail: "some_future_detail" }));
    await expect(submitForm(body({}))).rejects.toThrow(ERROR_COPY.invalid_photo);
  });

  it("unknown_job keeps its pick-another copy", async () => {
    fetchMock.mockResolvedValue(reply(422, { error: "unknown_job" }));
    await expect(submitForm(body({}))).rejects.toThrow("That job is no longer active — pick another.");
  });

  it("an unrecognized error (or a non-JSON body) keeps the generic fallback", async () => {
    fetchMock.mockResolvedValue(reply(400, { error: "invalid_submission" }));
    await expect(submitForm(body({}))).rejects.toThrow("Submission failed. Please try again.");
    fetchMock.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error("not json");
      },
    } as unknown as Response);
    await expect(submitForm(body({}))).rejects.toThrow("Submission failed. Please try again.");
  });
});
