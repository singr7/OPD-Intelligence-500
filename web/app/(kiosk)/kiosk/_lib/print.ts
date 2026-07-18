// The token slip — ESC/POS thermal print (S7, doc 03 §1a).
//
// > "Ends with big token number + printed slip (ESC/POS thermal printer via
// >  kiosk print bridge)." — doc 03 §1a
//
// Two paths, because a kiosk in Alwar has a thermal printer and a laptop demo
// does not:
//
//   escposSlip()  builds the raw ESC/POS byte stream for a 58mm thermal printer.
//                 A kiosk with a print bridge (a tiny local daemon owning the USB
//                 printer) POSTs these bytes to it. The bytes are the real
//                 protocol — centre, double-height the token, cut — not an
//                 approximation.
//   printSlip()   falls back to the browser's own print dialog with a slip-styled
//                 layout, for a kiosk with no thermal printer and for the demo.
//
// ## No printer has ever printed one of these
//
// Same honesty as every vendor integration in this repo (STATE.md: "no live
// vendor has ever accepted a call"): the ESC/POS bytes are built against the
// documented command set and unit-tested, but the first real slip needs a person
// watching a real printer. The command set is near-universal across cheap 58mm
// printers; the cut command and codepage for Devanagari are the two things most
// likely to need per-printer tuning on the box.

export type Slip = {
  tokenNo: number | null;
  departmentName: string;
  hospitalName: string;
  /** ISO time the token was issued. */
  issuedAt: string;
  /** true when the intake raised a red flag — the slip says "show this at the
   *  desk now", the one place a downtime patient's urgency is visible on paper. */
  urgent: boolean;
  lang: "hi" | "en";
};

// ESC/POS control bytes (Epson-compatible; the 58mm clones follow it).
const ESC = 0x1b;
const GS = 0x1d;

const INIT = [ESC, 0x40]; // @ — reset
const ALIGN_CENTER = [ESC, 0x61, 0x01];
const ALIGN_LEFT = [ESC, 0x61, 0x00];
const BOLD_ON = [ESC, 0x45, 0x01];
const BOLD_OFF = [ESC, 0x45, 0x00];
const SIZE_NORMAL = [GS, 0x21, 0x00];
const SIZE_DOUBLE = [GS, 0x21, 0x11]; // double width + height
const FEED = (n: number) => [ESC, 0x64, n]; // feed n lines
const CUT = [GS, 0x56, 0x01]; // partial cut

/** Build the raw ESC/POS byte stream for a slip. */
export function escposSlip(slip: Slip): Uint8Array {
  const bytes: number[] = [];
  const line = (text: string) => bytes.push(...encode(text), 0x0a);

  bytes.push(...INIT, ...ALIGN_CENTER);

  bytes.push(...BOLD_ON);
  line(slip.hospitalName);
  bytes.push(...BOLD_OFF);
  line(slip.departmentName);
  bytes.push(...FEED(1));

  // The token, the biggest thing on the slip — it is what the patient watches
  // the board for.
  bytes.push(...SIZE_DOUBLE, ...BOLD_ON);
  line(slip.tokenNo === null ? "—" : String(slip.tokenNo));
  bytes.push(...BOLD_OFF, ...SIZE_NORMAL);
  bytes.push(...FEED(1));

  if (slip.urgent) {
    bytes.push(...BOLD_ON);
    line(slip.lang === "hi" ? "** तुरंत डेस्क पर दिखाएँ **" : "** SHOW AT DESK NOW **");
    bytes.push(...BOLD_OFF);
  }

  bytes.push(...ALIGN_LEFT);
  line(formatTime(slip.issuedAt));
  bytes.push(...ALIGN_CENTER);
  line(slip.lang === "hi" ? "अपना नंबर आने तक बैठें" : "Please wait for your number");

  bytes.push(...FEED(3), ...CUT);
  return new Uint8Array(bytes);
}

/** Latin-1 for now: the token, time and Latin department names encode fine. The
 *  Devanagari lines need the printer's codepage set (a per-printer command on the
 *  box, S-OSS/deploy) — until then they print as '?', which is why the slip
 *  leans on the number and the time, both ASCII. The board and the screen carry
 *  the full Hindi. */
function encode(text: string): number[] {
  return Array.from(text, (ch) => {
    const code = ch.charCodeAt(0);
    return code < 0x100 ? code : 0x3f; // '?'
  });
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${d.toLocaleDateString()} ${hh}:${mm}`;
}

/** Send a slip to a thermal printer via the kiosk print bridge, or fall back to
 *  the browser print dialog. Returns which path was taken. */
export async function printSlip(
  slip: Slip,
  opts: { bridgeUrl?: string } = {}
): Promise<"thermal" | "browser" | "skipped"> {
  const bridge = opts.bridgeUrl ?? bridgeUrlFromEnv();
  if (bridge) {
    try {
      const res = await fetch(bridge, {
        method: "POST",
        headers: { "content-type": "application/octet-stream" },
        body: escposSlip(slip),
      });
      if (res.ok) return "thermal";
    } catch {
      // The bridge is unreachable (unplugged, daemon down) — fall through to the
      // browser dialog rather than leaving the patient with no slip.
    }
  }
  if (typeof window !== "undefined" && typeof window.print === "function") {
    window.print();
    return "browser";
  }
  return "skipped";
}

function bridgeUrlFromEnv(): string | null {
  // A kiosk with a thermal printer sets this to its local print daemon, e.g.
  // http://127.0.0.1:9100/print. Absent on a laptop demo.
  const value = process.env.NEXT_PUBLIC_PRINT_BRIDGE_URL;
  return value && value.length > 0 ? value : null;
}
