"use client";

// The Reports tab (plan §1.5, doc 21 §1.5) — what M1's pipeline was built for.
//
// Its single job: let the doctor trust or distrust the machine's reading of this
// patient's papers in twenty seconds, with the original page always one tap away
// from any number.
//
// Three things, in this order:
//
//   1. **The summary, stamped as a draft.** An unverified machine reading of a
//      lab report is a draft and every screen showing one says so, until a
//      doctor taps *Mark reviewed*. Re-extraction clears that on purpose, so the
//      badge can go back to draft under a doctor who already reviewed it — a
//      re-run is a new reading, and carrying the old signature onto numbers
//      nobody saw would be the worst bug this module could have.
//   2. **The flagged values, weakest signal marked.** Whether a value is
//      abnormal was computed in Python on `Decimal` (`app/mrd/ranges.py`), never
//      by the model. But *which range* it was computed against matters: a range
//      the lab printed beside the value is strong, and one from
//      `seeds/lab_reference_ranges.json` — eighteen adult tests, shipping
//      `review_pending` — is not, until an oncologist signs it off. Those rows
//      say so, in the row, not in a footnote.
//   3. **The original pages.**
//
// The deliberate aesthetic risk here (doc 04 §5, one per surface) is the
// **range track**: a flagged value is drawn on the interval its own report
// printed, so "slightly low" and "a third of the floor" stop looking alike. It
// is drawn only when a low, a high and a numeric value all exist — never
// inferred, never drawn for a value we could not place. Everything else on this
// screen stays quiet.
//
// What this tab must never do: turn a status into an alarm. Red on this console
// belongs to the deterministic red-flag lane in the spine. A flagged lab value
// is amber at its loudest.

import { useState } from "react";
import {
  FLAG_LABELS,
  isOutlier,
  KIND_LABELS,
  type FlaggedValue,
  type MedicalDocument,
} from "@/app/_lib/records";
import { PageStrip } from "./PageViewer";

const STATUS_COPY: Record<MedicalDocument["status"], string> = {
  capturing: "Still being photographed",
  captured: "Waiting to be read",
  extracting: "Being read now",
  extracted: "Read — summary still coming",
  summarized: "Read",
  extraction_failed: "Could not be read",
};

export function ReportsTab({
  token,
  documents,
  loading,
  error,
  verifying,
  onVerify,
}: {
  token: string;
  documents: MedicalDocument[];
  loading: boolean;
  error: string | null;
  verifying: string | null;
  onVerify: (documentId: string) => void;
}) {
  // Newest first, and the newest one open: it is almost always the report the
  // patient just handed over at the desk. The rest are one tap away rather than
  // a wall.
  const [open, setOpen] = useState<string | null>(null);
  const openId = open ?? documents[0]?.id ?? null;

  if (loading) return <p className="work-empty">Looking for scanned reports…</p>;

  if (error) {
    return (
      <p className="work-empty err" data-testid="reports-error">
        {error} Their papers may still be on file — this is the console failing to ask, not a
        statement that there are none.
      </p>
    );
  }

  if (documents.length === 0) {
    return (
      <p className="work-empty" data-testid="reports-empty">
        Nothing has been scanned for this patient. Paper reports are photographed at the desk on
        the coordinator&rsquo;s phone; nothing here means nothing was scanned, not that nothing
        exists.
      </p>
    );
  }

  return (
    <section className="reports" data-testid="reports-tab">
      {/* No tally line here. The spine states exactly these counts, forty
          pixels above, and never unmounts — printing them again at the top of
          the tab was the screen saying the same sentence twice. Caught in the
          doc 04 §5 screenshot critique. */}
      {documents.map((doc) => (
        <DocumentBlock
          key={doc.id}
          token={token}
          doc={doc}
          open={doc.id === openId}
          onToggle={() => setOpen(doc.id === openId ? "" : doc.id)}
          verifying={verifying === doc.id}
          onVerify={() => onVerify(doc.id)}
        />
      ))}
    </section>
  );
}

function DocumentBlock({
  token,
  doc,
  open,
  onToggle,
  verifying,
  onVerify,
}: {
  token: string;
  doc: MedicalDocument;
  open: boolean;
  onToggle: () => void;
  verifying: boolean;
  onVerify: () => void;
}) {
  // Which page the viewer should jump to, set by tapping a value's page number.
  const [jumpTo, setJumpTo] = useState<number | null>(null);
  const ex = doc.extraction;
  const scanned = new Date(doc.created_at);

  return (
    <article className="rp-doc" data-testid="report-document" data-status={doc.status}>
      <button className="rp-head" onClick={onToggle} aria-expanded={open}>
        <span className="rp-head-l">
          <strong>{KIND_LABELS[doc.kind]}</strong>
          <span className="rp-when">
            {ex?.report_date ? `dated ${ex.report_date}` : `scanned ${scanned.toLocaleDateString()}`}
            {" · "}
            {doc.pages} {doc.pages === 1 ? "page" : "pages"}
          </span>
        </span>
        <span className="rp-head-r">
          {ex && ex.outlier_count > 0 && (
            <span className="rp-chip flagged">{ex.outlier_count} flagged</span>
          )}
          {ex && !ex.verified && (
            <span className="rp-chip draft" data-testid="draft-chip">
              Unverified
            </span>
          )}
          {doc.status === "extraction_failed" && <span className="rp-chip failed">Unread</span>}
          <span className="rp-caret" aria-hidden="true">
            {open ? "⌃" : "⌄"}
          </span>
        </span>
      </button>

      {open && (
        <div className="rp-body">
          {/* 1. the reading, or an honest account of why there isn't one */}
          {doc.status === "extraction_failed" ? (
            <Unread doc={doc} />
          ) : ex ? (
            <>
              <DraftBanner ex={ex} verifying={verifying} onVerify={onVerify} />
              {ex.summary_text ? (
                <p className="rp-summary" data-testid="report-summary">
                  {ex.summary_text}
                </p>
              ) : (
                <p className="rp-pending">
                  The values below are read. The written summary is still being generated.
                </p>
              )}
            </>
          ) : (
            <p className="rp-pending" data-testid="report-pending">
              {STATUS_COPY[doc.status]}. The original pages are below and are readable now.
            </p>
          )}

          {/* 2. the values */}
          {ex && <Values ex={ex} onOpenPage={setJumpTo} />}

          {ex && ex.narrative_findings.length > 0 && (
            <section className="rp-sec">
              <h3>Findings, as printed</h3>
              <ul className="rp-findings">
                {ex.narrative_findings.map((line, i) => (
                  <li key={i}>{line}</li>
                ))}
              </ul>
            </section>
          )}

          {ex && (ex.illegible_regions.length > 0 || ex.dropped_rows > 0) && (
            <p className="rp-illegible" data-testid="illegible">
              <strong>Not read:</strong>{" "}
              {ex.illegible_regions.length > 0
                ? ex.illegible_regions.join("; ")
                : `${ex.dropped_rows} row(s) the reading could not use`}
              {ex.dropped_rows > 0 && ex.illegible_regions.length > 0
                ? ` · ${ex.dropped_rows} unusable row(s)`
                : ""}
              . Read those off the pages below — they were left out rather than guessed.
            </p>
          )}

          {/* 3. the originals */}
          <section className="rp-sec">
            <h3>Original pages</h3>
            <PageStrip
              token={token}
              documentId={doc.id}
              pages={doc.pages}
              openAt={jumpTo}
              onOpenAtHandled={() => setJumpTo(null)}
            />
          </section>
        </div>
      )}
    </article>
  );
}

/** The draft stamp, and the one tap that clears it. */
function DraftBanner({
  ex,
  verifying,
  onVerify,
}: {
  ex: NonNullable<MedicalDocument["extraction"]>;
  verifying: boolean;
  onVerify: () => void;
}) {
  if (ex.verified) {
    return (
      <p className="rp-verified" data-testid="verified-banner">
        <span className="rp-verified-mark" aria-hidden="true">
          ✓
        </span>
        Reviewed against the original pages
        {ex.verified_at && <> on {new Date(ex.verified_at).toLocaleString()}</>}.
      </p>
    );
  }
  return (
    <div className="rp-draft" data-testid="draft-banner">
      <p>
        <strong>Read by machine · unverified.</strong> Nobody has checked these numbers against
        the pages yet. Treat it as a draft.
      </p>
      <button className="rp-verify" onClick={onVerify} disabled={verifying} data-testid="verify">
        {verifying ? "Saving…" : "Mark reviewed"}
      </button>
    </div>
  );
}

/** Why there is no reading, said plainly, with the pages still below it. */
function Unread({ doc }: { doc: MedicalDocument }) {
  return (
    <div className="rp-unread" data-testid="unread-banner">
      <p>
        <strong>The machine could not read these pages.</strong>
      </p>
      {/* On its own line: the stored reason is a phrase written for an operator
          ("could not be read by the model: gemini http 503"), and running it on
          after the sentence above made the screen say the same thing twice. */}
      <p className="rp-unread-reason">{doc.failure_reason ?? "No reason was recorded."}</p>
      <p className="rp-unread-note">
        The photographs are below and are the record. Ask the desk to re-scan if they are not
        legible — a re-read is started from the coordinator&rsquo;s scan screen.
      </p>
    </div>
  );
}

function Values({
  ex,
  onOpenPage,
}: {
  ex: NonNullable<MedicalDocument["extraction"]>;
  onOpenPage: (page: number) => void;
}) {
  const [showNormal, setShowNormal] = useState(false);
  const outliers = ex.values.filter((v) => isOutlier(v.flag));
  const rest = ex.values.filter((v) => !isOutlier(v.flag));

  if (ex.values.length === 0) {
    return (
      <p className="rp-pending" data-testid="no-values">
        No numeric values were read from this document. If it is a lab report, read it off the
        pages below.
      </p>
    );
  }

  return (
    <section className="rp-sec">
      <h3>
        Values
        {outliers.length > 0 && <span className="rp-sec-n">{outliers.length} flagged</span>}
      </h3>

      {ex.uses_fallback_ranges && (
        <p className="rp-fallback" data-testid="fallback-note">
          Rows marked <em>our range</em> were compared against this system&rsquo;s own reference
          table, which an oncologist has not reviewed. Weigh those flags accordingly.
        </p>
      )}

      <table className="rp-values" data-testid="values-table">
        <thead>
          <tr>
            <th>Test</th>
            <th>Value</th>
            <th>Reference</th>
            <th>Page</th>
          </tr>
        </thead>
        <tbody>
          {outliers.map((v, i) => (
            <ValueRow key={`o${i}`} v={v} onOpenPage={onOpenPage} />
          ))}
          {showNormal && rest.map((v, i) => <ValueRow key={`n${i}`} v={v} onOpenPage={onOpenPage} />)}
        </tbody>
      </table>

      {rest.length > 0 && (
        <button className="rp-more" onClick={() => setShowNormal((v) => !v)} data-testid="show-normal">
          {showNormal ? "Hide" : "Show"} {rest.length} within range
        </button>
      )}
    </section>
  );
}

function ValueRow({ v, onOpenPage }: { v: FlaggedValue; onOpenPage: (page: number) => void }) {
  const weak = v.ref_source === "default";
  return (
    <tr className={`flag-${v.flag} ${weak ? "weak" : ""}`} data-testid="value-row">
      <td className="vt-name">{v.name}</td>
      <td className="vt-value">
        <span className="vt-num">
          {v.value_text}
          {v.unit && <span className="vt-unit"> {v.unit}</span>}
        </span>
        {isOutlier(v.flag) && <span className={`vt-flag ${v.flag}`}>{FLAG_LABELS[v.flag]}</span>}
        {v.flag === "unknown" && <span className="vt-flag unknown">{FLAG_LABELS.unknown}</span>}
      </td>
      <td className="vt-ref">
        {v.ref_low != null && v.ref_high != null ? (
          <>
            <span className="vt-range">
              {v.ref_low}–{v.ref_high}
            </span>
            <span className={`vt-src ${weak ? "weak" : ""}`}>
              {weak ? "our range" : "printed on report"}
            </span>
            <Track value={v.value_text} low={v.ref_low} high={v.ref_high} weak={weak} />
          </>
        ) : (
          <span className="vt-norange">
            {/* Not a failure — a fact. Neither the report nor our table had a
                range for this test, so nothing was decided about it. */}
            no range available
          </span>
        )}
      </td>
      <td className="vt-page">
        {v.page != null ? (
          <button
            className="vt-pagebtn"
            onClick={() => onOpenPage(v.page as number)}
            data-testid="value-page"
            aria-label={`Open page ${v.page}, where ${v.name} was read`}
          >
            p{v.page}
          </button>
        ) : (
          <span className="vt-nopage">—</span>
        )}
      </td>
    </tr>
  );
}

/**
 * The range track — this surface's one aesthetic risk.
 *
 * A value drawn on the interval its own report printed. The band is the
 * reference range; the track extends one range-width either side, so a value at
 * half the floor and a value a hair under it stop looking like the same fact.
 *
 * It refuses rather than approximates: no low, no high, or a value that is not a
 * plain number (">150", "trace", "1.2 x 10^3") and nothing is drawn at all. A
 * mis-drawn position on a lab value is worse than no picture.
 */
function Track({
  value,
  low,
  high,
  weak,
}: {
  value: string;
  low: string;
  high: string;
  weak: boolean;
}) {
  const v = Number(value);
  const lo = Number(low);
  const hi = Number(high);
  if (!Number.isFinite(v) || !Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return null;

  const span = hi - lo;
  const left = lo - span;
  const pct = Math.max(0, Math.min(100, ((v - left) / (span * 3)) * 100));

  return (
    <span className={`vt-track ${weak ? "weak" : ""}`} aria-hidden="true">
      <span className="vt-track-band" />
      <span className="vt-track-mark" style={{ left: `${pct}%` }} />
    </span>
  );
}
