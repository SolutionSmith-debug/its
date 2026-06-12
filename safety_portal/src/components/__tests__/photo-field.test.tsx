/**
 * Photo upload PR-1 (2026-06-12) — SPA-side unit tests (jsdom; no canvas needed:
 * encode paths are exercised live on the mirror, these lock the pure logic + wiring).
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FormRenderer, initialValues } from "../../forms/FormRenderer";
import type { Field, FormDefinition, PhotoValue } from "../../forms/types";
import { PhotoField, appendPhotos, maxCountFor } from "../PhotoField";

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
