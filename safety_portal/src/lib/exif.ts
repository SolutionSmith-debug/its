// Minimal JPEG EXIF sidecar reader (photo upload PR-1, 2026-06-12).
//
// WHY THIS EXISTS: the owner decision is "caption then strip" — the PDF caption shows
// capture time + GPS, but the stored/transmitted photo carries no hidden metadata. The
// canvas re-encode in PhotoField does the STRIP half for free (canvas drops EXIF); this
// module reads the two caption tags from the ORIGINAL bytes BEFORE that re-encode.
//
// WHY VENDORED (not an npm dep): we need exactly two tags (DateTimeOriginal + GPS), and
// the form-publish pipeline treats every new dependency as added review/supply-chain
// surface. ~100 bounds-checked lines beat a parser dependency.
//
// TRUST: output is DISPLAY-ONLY sidecar text. It is UNTRUSTED downstream (Invariant 2)
// — rendered as caption text, never used as a logic/auth input. Every parse failure,
// truncation, or non-JPEG input returns "" fields; this function never throws.

export interface ExifSidecar {
  taken_at: string; // "YYYY-MM-DDTHH:MM:SS" or ""
  gps: string; // "lat,lon" (signed decimal, 6dp) or ""
}

const EMPTY: ExifSidecar = { taken_at: "", gps: "" };

export function extractExif(buf: ArrayBuffer): ExifSidecar {
  try {
    return parseJpeg(new DataView(buf));
  } catch {
    return EMPTY; // any out-of-bounds read on malformed input → no sidecar
  }
}

function parseJpeg(v: DataView): ExifSidecar {
  if (v.byteLength < 4 || v.getUint16(0) !== 0xffd8) return EMPTY; // not a JPEG
  let off = 2;
  while (off + 4 <= v.byteLength) {
    const marker = v.getUint16(off);
    if ((marker & 0xff00) !== 0xff00) return EMPTY; // marker stream desynced
    const size = v.getUint16(off + 2); // includes the 2 size bytes
    if (marker === 0xffe1 && size >= 10 && off + 2 + size <= v.byteLength) {
      // APP1 must open with "Exif\0\0" before the TIFF header.
      if (v.getUint32(off + 4) === 0x45786966 && v.getUint16(off + 8) === 0x0000) {
        return parseTiff(v, off + 10, off + 2 + size);
      }
    }
    if (marker === 0xffda) return EMPTY; // SOS = entropy-coded data; EXIF not found
    off += 2 + size;
  }
  return EMPTY;
}

/** TIFF entry value sizes by type (1=BYTE 2=ASCII 3=SHORT 4=LONG 5=RATIONAL 7=UNDEF 10=SRATIONAL). */
const TYPE_SIZE: Record<number, number> = { 1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 10: 8 };

function parseTiff(v: DataView, tiff: number, end: number): ExifSidecar {
  const bom = v.getUint16(tiff);
  const le = bom === 0x4949; // "II" little-endian; "MM" big-endian
  if (!le && bom !== 0x4d4d) return EMPTY;
  const u16 = (o: number) => v.getUint16(o, le);
  const u32 = (o: number) => v.getUint32(o, le);
  if (u16(tiff + 2) !== 42) return EMPTY;

  /** Start of an entry's value bytes — inline in the 4-byte value slot when it fits. */
  const valueLoc = (type: number, count: number, slot: number): number => {
    const size = (TYPE_SIZE[type] ?? 1) * count;
    return size <= 4 ? slot : tiff + u32(slot);
  };
  const ascii = (start: number, len: number): string => {
    let s = "";
    for (let i = 0; i < len && start + i < end; i++) {
      const c = v.getUint8(start + i);
      if (c === 0) break;
      if (c >= 0x20 && c < 0x7f) s += String.fromCharCode(c); // printable ASCII only
    }
    return s;
  };
  const rational = (o: number): number => {
    const den = u32(o + 4);
    return den === 0 ? 0 : u32(o) / den;
  };
  type Visit = (tag: number, type: number, count: number, slot: number) => void;
  const walk = (ifd: number, visit: Visit): void => {
    if (ifd <= tiff || ifd + 2 > end) return;
    const n = u16(ifd);
    if (n > 256) return; // sanity cap — hostile/garbage entry counts
    for (let i = 0; i < n; i++) {
      const e = ifd + 2 + i * 12;
      if (e + 12 > end) return;
      visit(u16(e), u16(e + 2), u32(e + 4), e + 8);
    }
  };

  let exifIfd = 0;
  let gpsIfd = 0;
  walk(tiff + u32(tiff + 4), (tag, _type, _count, slot) => {
    if (tag === 0x8769) exifIfd = tiff + u32(slot); // Exif sub-IFD pointer
    if (tag === 0x8825) gpsIfd = tiff + u32(slot); // GPS sub-IFD pointer
  });

  let taken = "";
  if (exifIfd) {
    walk(exifIfd, (tag, type, count, slot) => {
      if (tag === 0x9003 && type === 2 && count >= 19) {
        // DateTimeOriginal "YYYY:MM:DD HH:MM:SS"
        const m = /^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}:\d{2}:\d{2})/.exec(
          ascii(valueLoc(type, count, slot), Math.min(count, 20)),
        );
        if (m) taken = `${m[1]}-${m[2]}-${m[3]}T${m[4]}`;
      }
    });
  }

  let gps = "";
  if (gpsIfd) {
    let latRef = "";
    let lonRef = "";
    let lat: number | null = null;
    let lon: number | null = null;
    const dms = (o: number): number => rational(o) + rational(o + 8) / 60 + rational(o + 16) / 3600;
    walk(gpsIfd, (tag, type, count, slot) => {
      if (tag === 1 && type === 2) latRef = ascii(valueLoc(type, count, slot), 1);
      if (tag === 3 && type === 2) lonRef = ascii(valueLoc(type, count, slot), 1);
      if (tag === 2 && type === 5 && count === 3) lat = dms(valueLoc(type, count, slot));
      if (tag === 4 && type === 5 && count === 3) lon = dms(valueLoc(type, count, slot));
    });
    if (lat !== null && lon !== null && Number.isFinite(lat) && Number.isFinite(lon)) {
      const sLat = (latRef === "S" ? -1 : 1) * lat;
      const sLon = (lonRef === "W" ? -1 : 1) * lon;
      if (Math.abs(sLat) <= 90 && Math.abs(sLon) <= 180 && (sLat !== 0 || sLon !== 0)) {
        gps = `${sLat.toFixed(6)},${sLon.toFixed(6)}`;
      }
    }
  }
  return { taken_at: taken, gps };
}
