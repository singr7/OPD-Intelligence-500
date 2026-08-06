"use client";

// The original photograph, under the guard.
//
// This component exists because of one decision in doc 21 §1.3: page bytes are
// streamed by the backend behind auth and are **never** handed out as a signed
// URL. The doctor's session token lives in `localStorage`, so a plain
// `<img src={pageUrl(...)}>` sends no Authorization header and gets a 401 —
// the bytes have to be fetched and turned into an object URL that dies with the
// tab.
//
// Every fetch here is therefore paired with a `revokeObjectURL` on unmount. A
// console left open on a ward machine all morning, paging through reports,
// would otherwise hold every page of every patient it had shown in memory —
// which is both a leak and exactly the kind of lingering copy the no-signed-URL
// rule exists to prevent.
//
// The three failures are distinguished on purpose. A 410 is not "broken": it is
// Postgres restored without `OBJECT_STORE_DIR`, and saying so is how an operator
// finds out their backup is incomplete (doc 21 §8.3).

import { useCallback, useEffect, useState } from "react";
import { fetchPageObjectUrl, PageUnavailable } from "@/app/_lib/records";

const FAILURE_COPY: Record<"gone" | "denied" | "error", string> = {
  gone: "This page is no longer stored. The database was restored without the scanned pages — an operator needs to know.",
  denied: "Your session is not allowed to open this page.",
  error: "This page could not be loaded.",
};

/** One page as an `<img>`, fetched with the token and revoked on unmount. */
export function PageImage({
  token,
  documentId,
  page,
  alt,
  className,
  onClick,
}: {
  token: string;
  documentId: string;
  page: number;
  alt: string;
  className?: string;
  onClick?: () => void;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [failure, setFailure] = useState<"gone" | "denied" | "error" | null>(null);

  useEffect(() => {
    let live = true;
    let mine: string | null = null;
    const controller = new AbortController();

    setUrl(null);
    setFailure(null);

    fetchPageObjectUrl(token, documentId, page, controller.signal)
      .then((next) => {
        // Resolved after unmount: revoke it rather than leaking it into a
        // component that is gone.
        if (!live) {
          URL.revokeObjectURL(next);
          return;
        }
        mine = next;
        setUrl(next);
      })
      .catch((err: unknown) => {
        if (!live) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        setFailure(err instanceof PageUnavailable ? err.kind : "error");
      });

    return () => {
      live = false;
      controller.abort();
      if (mine) URL.revokeObjectURL(mine);
    };
  }, [token, documentId, page]);

  if (failure) {
    return (
      <p className={`pg-fail ${failure === "gone" ? "gone" : ""}`} data-testid="page-unavailable">
        {FAILURE_COPY[failure]}
      </p>
    );
  }
  if (!url) return <div className={`pg-load ${className ?? ""}`} aria-label="Loading page" />;

  /* eslint-disable-next-line @next/next/no-img-element -- an object URL, not an
     asset next/image can optimise; and it must never be cached to disk. */
  return <img src={url} alt={alt} className={className} onClick={onClick} />;
}

/**
 * The pages of one document, and a full-resolution view of any one of them.
 *
 * `openAt` lets a flagged value hand the viewer a page number — that is the
 * "original one tap away from every extracted number" rule from doc 21 §1.5,
 * and it is the whole reason to trust the table above it.
 */
export function PageStrip({
  token,
  documentId,
  pages,
  openAt,
  onOpenAtHandled,
}: {
  token: string;
  documentId: string;
  pages: number;
  openAt?: number | null;
  onOpenAtHandled?: () => void;
}) {
  const [zoom, setZoom] = useState<number | null>(null);

  useEffect(() => {
    if (openAt != null) setZoom(openAt);
  }, [openAt]);

  const close = useCallback(() => {
    setZoom(null);
    onOpenAtHandled?.();
  }, [onOpenAtHandled]);

  useEffect(() => {
    if (zoom == null) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") close();
      if (e.key === "ArrowRight") setZoom((p) => (p != null && p < pages ? p + 1 : p));
      if (e.key === "ArrowLeft") setZoom((p) => (p != null && p > 1 ? p - 1 : p));
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoom, pages, close]);

  if (pages === 0) {
    return <p className="pg-none">This document has no pages stored.</p>;
  }

  return (
    <>
      <ol className="pg-strip" data-testid="page-strip">
        {Array.from({ length: pages }, (_, i) => i + 1).map((n) => (
          <li key={n}>
            <button
              className="pg-thumb"
              onClick={() => setZoom(n)}
              aria-label={`Open page ${n} of ${pages} full size`}
              data-testid={`page-thumb-${n}`}
            >
              <PageImage token={token} documentId={documentId} page={n} alt={`Page ${n}`} />
              <span className="pg-n">{n}</span>
            </button>
          </li>
        ))}
      </ol>

      {zoom != null && (
        <div
          className="pg-zoom"
          role="dialog"
          aria-modal="true"
          aria-label={`Page ${zoom} of ${pages}`}
          data-testid="page-zoom"
          onClick={close}
        >
          <div className="pg-zoom-bar" onClick={(e) => e.stopPropagation()}>
            <span>
              Page {zoom} of {pages}
            </span>
            <span className="pg-zoom-keys">
              <button onClick={() => setZoom((p) => (p != null && p > 1 ? p - 1 : p))} disabled={zoom <= 1}>
                ‹
              </button>
              <button
                onClick={() => setZoom((p) => (p != null && p < pages ? p + 1 : p))}
                disabled={zoom >= pages}
              >
                ›
              </button>
              <button className="pg-close" onClick={close} aria-label="Close">
                ✕
              </button>
            </span>
          </div>
          <div className="pg-zoom-img" onClick={(e) => e.stopPropagation()}>
            <PageImage
              token={token}
              documentId={documentId}
              page={zoom}
              alt={`Page ${zoom} of ${pages}, full size`}
            />
          </div>
        </div>
      )}
    </>
  );
}
