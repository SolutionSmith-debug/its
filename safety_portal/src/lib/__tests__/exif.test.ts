/**
 * Photo upload PR-1 (2026-06-12) — vendored EXIF sidecar reader tests.
 * Builds a real little-endian TIFF-in-APP1 JPEG byte-by-byte (DateTimeOriginal +
 * GPS N/W rationals) and asserts the two caption tags parse; malformed/EXIF-less
 * inputs must yield empty sidecars, never throw.
 */
import { describe, expect, it } from "vitest";

import { extractExif } from "../exif";

function buildExifJpeg(): ArrayBuffer {
  const t: number[] = [];
  const u8 = (n: number) => t.push(n & 0xff);
  const u16 = (n: number) => {
    u8(n);
    u8(n >>> 8);
  };
  const u32 = (n: number) => {
    u8(n);
    u8(n >>> 8);
    u8(n >>> 16);
    u8(n >>> 24);
  };
  const entry = (tag: number, type: number, count: number, value: number) => {
    u16(tag);
    u16(type);
    u32(count);
    u32(value);
  };
  const entryInlineAscii = (tag: number, s: string) => {
    u16(tag);
    u16(2);
    u32(s.length + 1);
    u8(s.charCodeAt(0));
    u8(0);
    u8(0);
    u8(0);
  };
  // TIFF header (II, 42, IFD0@8)
  u8(0x49);
  u8(0x49);
  u16(42);
  u32(8);
  // IFD0 @8: Exif sub-IFD @38, GPS sub-IFD @56
  u16(2);
  entry(0x8769, 4, 1, 38);
  entry(0x8825, 4, 1, 56);
  u32(0);
  // Exif IFD @38: DateTimeOriginal (ASCII×20 @110)
  u16(1);
  entry(0x9003, 2, 20, 110);
  u32(0);
  // GPS IFD @56: refs inline, lat rationals @130, lon rationals @154
  u16(4);
  entryInlineAscii(1, "N");
  entry(2, 5, 3, 130);
  entryInlineAscii(3, "W");
  entry(4, 5, 3, 154);
  u32(0);
  // data: date @110 (20 bytes), lat @130 (24), lon @154 (24) → tiff length 178
  for (const ch of "2026:06:12 10:30:00") u8(ch.charCodeAt(0));
  u8(0);
  for (const [n, d] of [[27, 1], [57, 1], [207, 100]] as const) {
    u32(n);
    u32(d);
  }
  for (const [n, d] of [[82, 1], [27, 1], [2584, 100]] as const) {
    u32(n);
    u32(d);
  }
  // JPEG wrap: SOI + APP1(size, "Exif\0\0", tiff) + EOI
  const out: number[] = [0xff, 0xd8, 0xff, 0xe1];
  const size = 2 + 6 + t.length;
  out.push((size >>> 8) & 0xff, size & 0xff);
  for (const ch of "Exif") out.push(ch.charCodeAt(0));
  out.push(0, 0, ...t, 0xff, 0xd9);
  return new Uint8Array(out).buffer;
}

describe("extractExif", () => {
  it("reads DateTimeOriginal + signed GPS from a real EXIF block", () => {
    expect(extractExif(buildExifJpeg())).toEqual({
      taken_at: "2026-06-12T10:30:00",
      gps: "27.950575,-82.457178",
    });
  });

  it("returns empty sidecars for non-JPEG bytes", () => {
    expect(extractExif(new TextEncoder().encode("definitely not a jpeg").buffer as ArrayBuffer)).toEqual({
      taken_at: "",
      gps: "",
    });
  });

  it("returns empty sidecars for a JPEG with no EXIF (straight to SOS)", () => {
    const bytes = new Uint8Array([0xff, 0xd8, 0xff, 0xda, 0x00, 0x04, 0x00, 0x00, 0xff, 0xd9]);
    expect(extractExif(bytes.buffer)).toEqual({ taken_at: "", gps: "" });
  });

  it("never throws on truncated/hostile EXIF structures", () => {
    const good = new Uint8Array(buildExifJpeg());
    for (const cut of [3, 9, 14, 40, 90]) {
      expect(() => extractExif(good.slice(0, cut).buffer)).not.toThrow();
    }
  });
});
