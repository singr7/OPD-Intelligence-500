"use client";

// Three screens, one job: get a patient's paper into the system in about
// fifteen seconds, standing up, one-handed (doc 21 §1.2).
//
// Its single job: **file these pages against the right patient.**
// The three things that matter, in order:
//   1. the right patient — a mis-tap here puts one person's lab values on
//      another's screen, which is the worst thing this module can do;
//   2. the page count — the coordinator's only proof the whole report went in;
//   3. getting back to the picker fast, because there is a queue behind them.
//
// The deliberate aesthetic risk (doc 04 §5): the page counter is set in the
// board's train-board numerals. It is the one number the coordinator checks
// against the paper in their hand, and it earns being the biggest thing on the
// screen. Everything else stays quiet.
//
// Not built here: a service-worker upload queue that survives a reload. Failed
// pages are retried within the session, and the limit is registered in STATE
// rather than implied away — a coordinator who closes the tab on a bad
// connection loses the pending page, and is told so.

import { Check, Loader2, RotateCcw, Search, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/app/_components/ui/Button";
import {
  KIND_LABELS,
  completeDocument,
  downscale,
  scanWorklist,
  startDocument,
  uploadPage,
  type DocumentKind,
  type WorklistRow,
} from "@/app/_lib/records";
import styles from "./scanner.module.css";

type Step = "pick" | "capture" | "done";

type Page = {
  id: number;
  preview: string;
  blob: Blob;
  state: "uploading" | "stored" | "failed";
};

const KINDS: DocumentKind[] = [
  "lab",
  "histopath",
  "imaging_report",
  "discharge",
  "outside_rx",
  "other",
];

export function Scanner({
  token,
  onSignOut,
}: {
  token: string;
  onSignOut: () => void;
}) {
  const [step, setStep] = useState<Step>("pick");
  const [rows, setRows] = useState<WorklistRow[] | null>(null);
  const [query, setQuery] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);

  const [patient, setPatient] = useState<WorklistRow | null>(null);
  const [kind, setKind] = useState<DocumentKind>("lab");
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [pages, setPages] = useState<Page[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nextId = useRef(1);

  const load = useCallback(
    async (q: string) => {
      setLoadError(null);
      try {
        setRows(await scanWorklist(token, q));
      } catch (err) {
        setRows([]);
        setLoadError(err instanceof Error ? err.message : "Could not load the list.");
      }
    },
    [token],
  );

  useEffect(() => {
    if (step === "pick") void load(query);
    // Re-runs on an explicit search only; typing does not fire a request per key.
  }, [step, load]); // eslint-disable-line react-hooks/exhaustive-deps

  // Object URLs are revoked when the batch is dropped, not on every render —
  // a strip of five previews left unrevoked is five full-size bitmaps held.
  const dropPreviews = (list: Page[]) =>
    list.forEach((page) => URL.revokeObjectURL(page.preview));

  async function choose(row: WorklistRow) {
    setPatient(row);
    setPages([]);
    setDocumentId(null);
    setError(null);
    setStep("capture");
  }

  async function ensureDocument(): Promise<string> {
    if (documentId) return documentId;
    const created = await startDocument(token, {
      patient_id: patient!.patient_id,
      visit_id: patient!.visit_id,
      kind,
    });
    setDocumentId(created.id);
    return created.id;
  }

  async function addPages(files: FileList | null) {
    // Copied out synchronously, before the first await. A `FileList` is a *live*
    // view of the input's files, and the caller resets `input.value` as soon as
    // this function yields — which emptied the list mid-flight and silently
    // dropped every page. It failed quietly, with no error and a count that
    // stayed at zero, which is the worst way for a capture step to fail.
    const picked = Array.from(files ?? []);
    if (picked.length === 0) return;
    setError(null);
    setBusy(true);
    try {
      const id = await ensureDocument();
      for (const file of picked) {
        const blob = await downscale(file);
        const page: Page = {
          id: nextId.current++,
          preview: URL.createObjectURL(blob),
          blob,
          state: "uploading",
        };
        setPages((current) => [...current, page]);
        try {
          await uploadPage(token, id, blob);
          setPages((current) =>
            current.map((p) => (p.id === page.id ? { ...p, state: "stored" } : p)),
          );
        } catch {
          setPages((current) =>
            current.map((p) => (p.id === page.id ? { ...p, state: "failed" } : p)),
          );
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the document.");
    } finally {
      setBusy(false);
    }
  }

  async function retryPage(page: Page) {
    if (!documentId) return;
    setPages((current) =>
      current.map((p) => (p.id === page.id ? { ...p, state: "uploading" } : p)),
    );
    try {
      await uploadPage(token, documentId, page.blob);
      setPages((current) =>
        current.map((p) => (p.id === page.id ? { ...p, state: "stored" } : p)),
      );
    } catch {
      setPages((current) =>
        current.map((p) => (p.id === page.id ? { ...p, state: "failed" } : p)),
      );
    }
  }

  async function finish() {
    if (!documentId) return;
    setBusy(true);
    setError(null);
    try {
      await completeDocument(token, documentId);
      setStep("done");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not close the document.");
    } finally {
      setBusy(false);
    }
  }

  function backToPicker() {
    dropPreviews(pages);
    setPages([]);
    setDocumentId(null);
    setPatient(null);
    setError(null);
    setStep("pick");
  }

  const stored = pages.filter((p) => p.state === "stored").length;
  const failed = pages.filter((p) => p.state === "failed").length;

  return (
    <main className={styles.page} data-testid="scan-root">
      <header className={styles.bar}>
        <span className={styles.brand}>Records scanning</span>
        <button type="button" className={styles.signOut} onClick={onSignOut}>
          Sign out
        </button>
      </header>

      {step === "pick" && (
        <section className={styles.panel} aria-labelledby="pick-heading">
          <h1 id="pick-heading" className={styles.heading}>
            Whose reports?
          </h1>

          <form
            className={styles.search}
            onSubmit={(e) => {
              e.preventDefault();
              void load(query);
            }}
          >
            <input
              className={styles.searchInput}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Token, phone or UHC ID"
              inputMode="search"
              aria-label="Search by token, phone or UHC ID"
              data-testid="scan-search"
            />
            <Button tone="secondary" type="submit" icon={<Search />}>
              Find
            </Button>
          </form>

          {loadError && (
            <p className={styles.error} role="alert">
              {loadError}
            </p>
          )}

          {rows === null && <p className={styles.quiet}>Loading today&rsquo;s list…</p>}

          {rows?.length === 0 && (
            <p className={styles.quiet} data-testid="scan-empty">
              {query.trim()
                ? "No patient matched that. Try the token, the phone number, or the UHC ID printed on their card."
                : "Nobody has checked in yet today."}
            </p>
          )}

          <ul className={styles.list}>
            {(rows ?? []).map((row) => (
              <li key={`${row.patient_id}-${row.visit_id ?? "none"}`}>
                <button
                  type="button"
                  className={styles.row}
                  onClick={() => void choose(row)}
                  data-testid="scan-patient"
                >
                  <span className={styles.token}>
                    {row.token_no ?? "—"}
                  </span>
                  <span className={styles.rowBody}>
                    <span className={styles.rowName}>{row.patient_name}</span>
                    <span className={styles.rowMeta}>
                      {row.document_count > 0
                        ? `${row.document_count} document${row.document_count > 1 ? "s" : ""} on file`
                        : "No documents yet"}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {step === "capture" && patient && (
        <section className={styles.panel} aria-labelledby="capture-heading">
          <h1 id="capture-heading" className={styles.heading}>
            {patient.patient_name}
          </h1>
          <p className={styles.quiet}>
            {patient.token_no !== null ? `Token ${patient.token_no}` : "Not in today's queue"}
          </p>

          <fieldset className={styles.kinds} disabled={pages.length > 0}>
            <legend className={styles.legend}>
              What is this?{" "}
              {pages.length > 0 && (
                // Locked from the first photo, not from the first *stored* page:
                // the document is created server-side with its kind before the
                // page is posted, so it is genuinely fixed even when the upload
                // then fails. Saying "once pages are in" beside a count of zero
                // is the screen contradicting itself.
                <span>· fixed after the first photo</span>
              )}
            </legend>
            <div className={styles.kindGrid}>
              {KINDS.map((k) => (
                <button
                  key={k}
                  type="button"
                  className={`${styles.kind} ${kind === k ? styles.kindOn : ""}`}
                  aria-pressed={kind === k}
                  onClick={() => setKind(k)}
                  data-testid={`scan-kind-${k}`}
                >
                  {KIND_LABELS[k]}
                </button>
              ))}
            </div>
          </fieldset>

          <div className={styles.counter} aria-live="polite">
            <span className={styles.count} data-testid="scan-page-count">
              {stored}
            </span>
            <span className={styles.countLabel}>
              {stored === 1 ? "page stored" : "pages stored"}
            </span>
          </div>

          {pages.length > 0 && (
            <ul className={styles.strip} data-testid="scan-strip">
              {pages.map((page, index) => (
                <li key={page.id} className={styles.frame} data-state={page.state}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={page.preview} alt={`Page ${index + 1}`} />
                  <span className={styles.frameNo}>{index + 1}</span>
                  {page.state === "uploading" && (
                    <span className={styles.frameState}>
                      <Loader2 aria-label="Uploading" />
                    </span>
                  )}
                  {page.state === "stored" && (
                    <span className={styles.frameState}>
                      <Check aria-label="Stored" />
                    </span>
                  )}
                  {page.state === "failed" && (
                    <button
                      type="button"
                      className={styles.frameRetry}
                      onClick={() => void retryPage(page)}
                      data-testid="scan-retry-page"
                    >
                      <RotateCcw aria-hidden="true" /> Retry
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}

          {failed > 0 && (
            <p className={styles.warn} role="alert" data-testid="scan-failed-note">
              {failed} page{failed > 1 ? "s" : ""} did not upload. Retry {failed > 1 ? "them" : "it"}{" "}
              before finishing — closing this screen loses {failed > 1 ? "them" : "it"}.
            </p>
          )}
          {error && (
            <p className={styles.error} role="alert" data-testid="scan-error">
              {error}
            </p>
          )}

          <label className={styles.capture} data-testid="scan-capture">
            <input
              type="file"
              accept="image/*"
              capture="environment"
              multiple
              onChange={(e) => {
                void addPages(e.target.files);
                e.target.value = "";
              }}
            />
            <span>{pages.length === 0 ? "Photograph the first page" : "Add another page"}</span>
          </label>

          <div className={styles.actions}>
            <Button tone="quiet" onClick={backToPicker} icon={<X />}>
              Cancel
            </Button>
            <Button
              tone="primary"
              onClick={() => void finish()}
              disabled={stored === 0 || busy || failed > 0}
              data-testid="scan-finish"
            >
              {busy ? "Saving…" : `Done · ${stored} page${stored === 1 ? "" : "s"}`}
            </Button>
          </div>
        </section>
      )}

      {step === "done" && patient && (
        <section className={styles.panel} aria-labelledby="done-heading">
          <div className={styles.doneMark} aria-hidden="true">
            <Check />
          </div>
          <h1 id="done-heading" className={styles.heading} data-testid="scan-done">
            {stored} page{stored === 1 ? "" : "s"} filed
          </h1>
          <p className={styles.quiet}>
            {KIND_LABELS[kind]} for {patient.patient_name}. The doctor will see it shortly —
            reading it takes a moment, and it appears whether or not that succeeds.
          </p>
          <div className={styles.actions}>
            <Button tone="primary" onClick={backToPicker} data-testid="scan-next-patient">
              Next patient
            </Button>
          </div>
        </section>
      )}
    </main>
  );
}
