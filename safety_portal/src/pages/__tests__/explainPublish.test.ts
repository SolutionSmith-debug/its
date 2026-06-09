import { describe, expect, it } from "vitest";

import { PublishError } from "../../lib/api";
import { explainPublish } from "../FormsPage";

// The publish banner must always tell the operator WHY a publish was rejected — the old
// `explainPublish` fell through to a contentless "Publish was rejected. Please review and try
// again." for any unmapped error (incl. the 401 admin idle-timeout that bit the incident-report
// publish). These lock the message in.
describe("explainPublish", () => {
  it("maps a 401 (admin session idle/expired) to a sign-in-again message", () => {
    const msg = explainPublish(new PublishError("idle", 401));
    expect(msg).toMatch(/session expired/i);
    expect(msg).toMatch(/sign in/i);
  });

  it("surfaces the server `reason` verbatim on a validation 400", () => {
    expect(
      explainPublish(new PublishError("invalid_definition", 400, "group 'g1' needs a non-empty response scale")),
    ).toBe("Rejected: group 'g1' needs a non-empty response scale");
  });

  it("keeps the in-progress (409) message", () => {
    expect(explainPublish(new PublishError("publish_in_progress", 409))).toMatch(/still in progress/i);
  });

  it("never returns a contentless message — names the code + status on an unmapped error", () => {
    const msg = explainPublish(new PublishError("teapot", 418));
    expect(msg).toContain("teapot");
    expect(msg).toContain("418");
    expect(msg).not.toBe("Publish was rejected. Please review and try again.");
  });

  it("falls back gracefully for a non-PublishError", () => {
    expect(explainPublish(new Error("boom"))).toMatch(/something went wrong/i);
  });
});
