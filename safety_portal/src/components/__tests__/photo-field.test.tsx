/**
 * Photo upload PR-1 (2026-06-12) — SPA-side unit tests (jsdom; no canvas needed:
 * encode paths are exercised live on the mirror, these lock the pure logic + wiring).
 * 2026-07-03: + the CS2 budget-ladder rejection lock (stubbed canvas drives the REAL
 * encodePhoto ladder) — a rejection must always show visible copy (never-silent).
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FormRenderer, initialValues } from "../../forms/FormRenderer";
import type { Field, FormDefinition, PhotoValue } from "../../forms/types";
import { PHOTO_MAX_BYTES, PhotoField, appendPhotos, maxCountFor } from "../PhotoField";

const photoField: Field = { key: "site_photos", label: "Site photos", input: "photo", max_count: 2 };
const px = (n: number): PhotoValue => ({ data: "QUJD", name: `p${n}.jpg`, taken_at: "", gps: "" });

afterEach(cleanup);

describe("appendPhotos / maxCountFor (pure helpers)", () => {
  it("appendPhotos truncates at max", () => {
    expect(appendPhotos([px(1)], [px(2), px(3)], 2)).toEqual([px(1), px(2)]);
  });
  it("maxCountFor clamps into 1..4 with default 4", () => {
    expect(maxCountFor(photoField)).toBe(2);
    expect(maxCountFor({ ...photoField, max_count: 99 })).toBe(4);
    expect(maxCountFor({ ...photoField, max_count: 0 })).toBe(4);
    expect(maxCountFor({ ...photoField, max_count: undefined })).toBe(4);
  });
});

describe("<PhotoField/>", () => {
  it("shows the running count, disables add at the limit, and removes on demand", () => {
    const onChange = vi.fn();
    render(<PhotoField field={photoField} photos={[px(1), px(2)]} onChange={onChange} />);
    expect(screen.getByText(/\(2\/2\)/)).toBeTruthy();
    const add = screen.getByRole("button", { name: /photo limit reached/i }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /remove photo 1/i }));
    expect(onChange).toHaveBeenCalledWith([px(2)]);
  });
  it("offers the add control when under the limit", () => {
    render(<PhotoField field={photoField} photos={[]} onChange={() => undefined} />);
    const add = screen.getByRole("button", { name: /add photos/i }) as HTMLButtonElement;
    expect(add.disabled).toBe(false);
  });
});

describe("<PhotoField/> CS2 budget-ladder rejection is NEVER silent", () => {
  /** Stub the encode surface (jsdom has no canvas/createImageBitmap) so the REAL encodePhoto
   *  ladder runs; every toBlob result is `blobBytes` long. Returns a restore fn. */
  function stubPhotoEncode(blobBytes: number): () => void {
    const g = globalThis as { createImageBitmap?: unknown };
    const hadCIB = "createImageBitmap" in g;
    const prevCIB = g.createImageBitmap;
    g.createImageBitmap = vi.fn(async () => ({ width: 4000, height: 3000, close: vi.fn() }));
    const getContext = vi
      .spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockImplementation(() => ({ drawImage: vi.fn() }) as unknown as CanvasRenderingContext2D);
    const toBlob = vi
      .spyOn(HTMLCanvasElement.prototype, "toBlob")
      .mockImplementation(function (cb: BlobCallback) {
        cb(new Blob([new Uint8Array(blobBytes)], { type: "image/jpeg" }));
      });
    return () => {
      getContext.mockRestore();
      toBlob.mockRestore();
      if (hadCIB) g.createImageBitmap = prevCIB;
      else delete g.createImageBitmap;
    };
  }

  it("a photo the FULL downscale ladder cannot fit under PHOTO_MAX_BYTES shows visible copy — no silent drop, no onChange", async () => {
    const restore = stubPhotoEncode(PHOTO_MAX_BYTES + 1); // every rung over budget → encodePhoto null
    try {
      const onChange = vi.fn();
      render(<PhotoField field={photoField} photos={[]} onChange={onChange} />);
      const input = screen.getByTestId("photo-input-site_photos");
      fireEvent.change(input, {
        target: { files: [new File([new Uint8Array(9_000)], "pano.jpg", { type: "image/jpeg" })] },
      });
      // The never-silent bar: the rejection surfaces as role=alert copy…
      await waitFor(() =>
        expect(screen.getByRole("alert").textContent).toContain("could not be processed as a photo"),
      );
      // …nothing was silently attached, and the control recovers (not stuck "Processing…").
      expect(onChange).not.toHaveBeenCalled();
      expect((screen.getByRole("button", { name: /add photos/i }) as HTMLButtonElement).disabled).toBe(false);
    } finally {
      restore();
    }
  });

  it("a ladder rung that fits keeps the happy path intact (same stub, small blob → onChange fires)", async () => {
    const restore = stubPhotoEncode(1_000);
    try {
      const onChange = vi.fn();
      render(<PhotoField field={photoField} photos={[]} onChange={onChange} />);
      fireEvent.change(screen.getByTestId("photo-input-site_photos"), {
        target: { files: [new File([new Uint8Array(9_000)], "site.jpg", { type: "image/jpeg" })] },
      });
      await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
      const added = onChange.mock.calls[0][0] as PhotoValue[];
      expect(added).toHaveLength(1);
      expect(added[0].name).toBe("site.jpg");
      expect(added[0].data.length).toBeGreaterThan(0);
    } finally {
      restore();
    }
  });
});

describe("FormRenderer photo wiring", () => {
  const def = {
    form_code: "photo-probe-v1",
    parent_form_code: "photo-probe",
    form_name: "Photo Probe",
    variant_label: null,
    version: 1,
    archetype: "rows_signatures",
    source_pdf: "probe.pdf",
    sections: [{ type: "header", fields: [photoField] }],
  } as unknown as FormDefinition;

  it("initialValues seeds photo fields with [] (not the string default)", () => {
    expect(initialValues(def)).toEqual({ site_photos: [] });
  });
  it("renders a PhotoField for input:'photo' header fields", () => {
    render(<FormRenderer def={def} values={initialValues(def)} setValues={() => undefined} />);
    expect(screen.getByRole("button", { name: /add photos/i })).toBeTruthy();
  });
});
