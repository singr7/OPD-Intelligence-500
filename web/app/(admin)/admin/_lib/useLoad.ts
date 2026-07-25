"use client";

// A tiny load-once-with-refresh hook, shared by the admin tabs. Keeps each tab to
// its rendering rather than re-implementing loading/error/refresh state five
// times. `deps` re-runs the fetch (e.g. when a filter changes); `reload` is the
// manual refresh a mutation calls after it writes.

import { useCallback, useEffect, useState } from "react";

export function useLoad<T>(
  fetcher: () => Promise<T>,
  onError: (err: unknown) => void,
  deps: unknown[] = [],
): { data: T | null; error: string | null; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  // fetcher is intentionally not a dep: callers pass a fresh closure each render,
  // and `deps` is the explicit re-run signal. eslint is told so below.
  const run = useCallback(() => {
    setLoading(true);
    setError(null);
    fetcher()
      .then((d) => setData(d))
      .catch((err) => {
        onError(err);
        setError(err instanceof Error ? err.message : "Failed to load");
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps]);

  useEffect(run, [run]);

  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}
