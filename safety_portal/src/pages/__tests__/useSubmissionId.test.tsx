import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useSubmissionId } from "../useSubmissionId";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

describe("useSubmissionId (A1 lost-ACK idempotency)", () => {
  it("is STABLE across re-renders — a failed-submit retry reuses the same id", () => {
    const { result, rerender } = renderHook(() => useSubmissionId());
    const first = result.current.submissionUuid;
    expect(first).toMatch(UUID_RE);
    rerender(); // a re-render (busy/error state change on retry) must NOT mint a new id
    expect(result.current.submissionUuid).toBe(first);
  });

  it("renew() mints a fresh id for the NEXT submission (after a confirmed success)", () => {
    const { result } = renderHook(() => useSubmissionId());
    const first = result.current.submissionUuid;
    act(() => {
      result.current.renew();
    });
    expect(result.current.submissionUuid).not.toBe(first);
    expect(result.current.submissionUuid).toMatch(UUID_RE);
  });
});
