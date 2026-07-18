// The offline lifecycle, as one hook (S7).
//
// A kiosk has a day-long life: it boots while the network is up, leases its token
// blocks, caches the trees, then must keep running when the uplink dies and sync
// what it did when it returns. This hook owns that lifecycle so `KioskApp` only
// sees the results — the flow to drive an intake, whether we are in downtime, and
// how many intakes are waiting to sync.
//
// The bootstrap is best-effort and idempotent. If the kiosk is already offline at
// boot it simply uses whatever is cached from last time; a fresh kiosk that has
// never been online cannot run offline, and says so honestly rather than issuing
// numbers from a block it does not hold.

"use client";

import { useEffect, useMemo, useState } from "react";

import { API_BASE, kioskApi } from "../api";
import type { Dept } from "../api";
import { hasIndexedDb, kioskId, loadBundle, pendingCount, saveBlocks, saveBundle } from "./db";
import { makeFlow, type Flow } from "./flow";
import { healthProbe, NetMonitor } from "./net";
import { syncPending, type SyncSummary } from "./sync";

export type OfflineState = {
  flow: Flow;
  downtime: boolean;
  reachable: boolean;
  /** Intakes completed offline and not yet synced. */
  pending: number;
  /** Departments from the cached bundle — the offline chooser's options. */
  cachedDepartments: Dept[];
  ready: boolean;
  lastSync: SyncSummary | null;
};

export function useOffline(): OfflineState {
  const net = useMemo(() => new NetMonitor(healthProbe(API_BASE)), []);
  const flow = useMemo(() => makeFlow({ net }), [net]);

  const [downtime, setDowntime] = useState(false);
  const [reachable, setReachable] = useState(true);
  const [pending, setPending] = useState(0);
  const [cachedDepartments, setCachedDepartments] = useState<Dept[]>([]);
  const [ready, setReady] = useState(false);
  const [lastSync, setLastSync] = useState<SyncSummary | null>(null);

  const refreshPending = useMemo(
    () => async () => {
      if (!hasIndexedDb()) return;
      setPending(await pendingCount());
    },
    []
  );

  // Bootstrap: cache the bundle, lease today's blocks, load the cached chooser.
  useEffect(() => {
    let cancelled = false;
    async function boot() {
      if (!hasIndexedDb()) {
        setReady(true);
        return;
      }
      // Whatever we already have, so an offline boot still has a chooser.
      const cached = await loadBundle();
      if (cached && !cancelled) setCachedDepartments(cached.departments);
      await refreshPending();

      // Best-effort refresh + lease while we can reach the server.
      try {
        const bundle = await kioskApi.bundle();
        await saveBundle({
          etag: bundle.etag,
          fetchedAt: bundle.generated_at,
          departments: bundle.departments,
          trees: bundle.trees.map((t) => t.tree),
        });
        if (!cancelled) setCachedDepartments(bundle.departments);

        const id = await kioskId();
        const lease = await kioskApi.leaseBlocks(id);
        await saveBlocks(
          lease.blocks.map((b) => ({
            departmentKey: b.department.key,
            departmentName: b.department.name,
            date: lease.date,
            startNo: b.start_no,
            endNo: b.end_no,
            nextFree: b.next_free,
          }))
        );
        net.observedSuccess();
      } catch {
        // Offline at boot, or the server is down. The cache carries us.
        net.observedFailure();
      } finally {
        if (!cancelled) setReady(true);
      }
    }
    void boot();
    return () => {
      cancelled = true;
    };
  }, [net, refreshPending]);

  // Watch connectivity; sync whenever we become reachable and something waits.
  useEffect(() => {
    const unsubscribe = net.subscribe((state) => {
      setDowntime(state.downtime);
      setReachable(state.reachable);
      if (state.reachable) {
        void (async () => {
          const summary = await syncPending();
          if (summary.attempted > 0) setLastSync(summary);
          await refreshPending();
        })();
      }
    });
    net.start();
    return () => {
      unsubscribe();
      net.stop();
    };
  }, [net, refreshPending]);

  // Keep the pending count fresh after local intakes complete.
  useEffect(() => {
    const timer = setInterval(() => void refreshPending(), 5_000);
    return () => clearInterval(timer);
  }, [refreshPending]);

  // Register the shell service worker so the kiosk paints with no network. It
  // caches HTML/JS/CSS only; the data path is IndexedDB (see kiosk-sw.js).
  useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/kiosk-sw.js").catch(() => {
      // A kiosk without a service worker still runs — it just cannot survive a
      // reboot mid-outage. Not fatal, and not worth alarming the patient over.
    });
  }, []);

  return { flow, downtime, reachable, pending, cachedDepartments, ready, lastSync };
}
