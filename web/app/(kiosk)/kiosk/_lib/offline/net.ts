// Is the API actually reachable? (S7, doc 01 §5)
//
// > "Heartbeat + banner. Every screen shows a subtle sync status. When offline
// > >60s, screens switch to Downtime Mode automatically." — doc 01 §5
//
// `navigator.onLine` is not the question. It answers "is there a link", and a
// kiosk on hospital wifi with a dead uplink, a wedged reverse proxy, or an api
// container that stopped (which happened twice during S6 — see HANDOFF) is
// `onLine: true` and completely unable to take an intake. So reachability is
// measured by asking the API, and `onLine: false` is used only as a fast hint to
// stop trying.
//
// ## Why the switch is deliberately slow, and the fallback is not
//
// Doc 01 §5 says Downtime Mode engages after 60s offline. That delay is a UX
// decision, not a technical one: flipping a patient-facing screen into "we are
// offline" because one request lost a race is worse than waiting — the kiosk is
// in a waiting room and the banner is read by frightened people.
//
// So the two are separated:
//
//   * **The banner** waits for `DOWNTIME_AFTER_MS` of continuous failure.
//   * **The intake path** does not wait at all. A request that fails falls
//     straight to the local walker, because the patient is standing there.
//
// A kiosk can therefore be quietly serving an intake locally while the screen
// still says "connected", which is correct: the patient does not care, and if the
// blip was momentary nothing was lost.

const HEARTBEAT_MS = 15_000;
/** doc 01 §5: "When offline >60s, screens switch to Downtime Mode". */
export const DOWNTIME_AFTER_MS = 60_000;
/** A kiosk request that has not answered in this long is not going to. */
const PROBE_TIMEOUT_MS = 4_000;

export type NetState = {
  /** Did the last probe reach the API? */
  reachable: boolean;
  /** True once we have been unreachable for longer than DOWNTIME_AFTER_MS. */
  downtime: boolean;
  since: number | null;
};

export type NetListener = (state: NetState) => void;

export class NetMonitor {
  private state: NetState = { reachable: true, downtime: false, since: null };
  private listeners = new Set<NetListener>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private downtimeTimer: ReturnType<typeof setTimeout> | null = null;
  private firstFailureAt: number | null = null;

  constructor(
    private readonly probe: () => Promise<boolean>,
    private readonly now: () => number = () => Date.now(),
    /** How long continuous failure must last before the banner flips. Defaults
     *  to the doc 01 §5 60s; the demo e2e shortens it (it cannot wait a minute).
     *  Only affects the banner — the intake path fails over immediately. */
    private readonly downtimeAfterMs: number = DOWNTIME_AFTER_MS
  ) {}

  get current(): NetState {
    return this.state;
  }

  subscribe(listener: NetListener): () => void {
    this.listeners.add(listener);
    listener(this.state);
    return () => this.listeners.delete(listener);
  }

  start(): void {
    if (this.timer !== null) return;
    void this.check();
    this.timer = setInterval(() => void this.check(), HEARTBEAT_MS);
    if (typeof window !== "undefined") {
      window.addEventListener("online", this.onOnline);
      window.addEventListener("offline", this.onOffline);
    }
  }

  stop(): void {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    this.clearDowntimeTimer();
    if (typeof window !== "undefined") {
      window.removeEventListener("online", this.onOnline);
      window.removeEventListener("offline", this.onOffline);
    }
  }

  private scheduleDowntimeCheck(): void {
    this.clearDowntimeTimer();
    this.downtimeTimer = setTimeout(() => {
      this.downtimeTimer = null;
      // Re-record the still-failing state so `downtime` is recomputed now that
      // the threshold has passed. `record` is idempotent when nothing changed.
      if (this.firstFailureAt !== null) this.record(false);
    }, this.downtimeAfterMs);
  }

  private clearDowntimeTimer(): void {
    if (this.downtimeTimer !== null) clearTimeout(this.downtimeTimer);
    this.downtimeTimer = null;
  }

  private onOnline = () => void this.check();
  /** The link dropped — believe *that* immediately; it is only the optimistic
   *  direction that needs proof. */
  private onOffline = () => this.record(false);

  async check(): Promise<boolean> {
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      this.record(false);
      return false;
    }
    let ok = false;
    try {
      ok = await this.probe();
    } catch {
      ok = false;
    }
    this.record(ok);
    return ok;
  }

  /** Report a failure observed by a real request. Better evidence than a probe:
   *  it is the thing the patient was waiting for. */
  observedFailure(): void {
    this.record(false);
  }

  observedSuccess(): void {
    this.record(true);
  }

  private record(reachable: boolean): void {
    const at = this.now();
    if (reachable) {
      this.firstFailureAt = null;
      this.clearDowntimeTimer();
    } else if (this.firstFailureAt === null) {
      this.firstFailureAt = at;
      // Flip the banner when the threshold elapses even if nothing else probes
      // in between — an outage detected by a failed request (not the heartbeat)
      // would otherwise not reach downtime until the next 15s tick.
      this.scheduleDowntimeCheck();
    }

    const downtime =
      !reachable &&
      this.firstFailureAt !== null &&
      at - this.firstFailureAt >= this.downtimeAfterMs;

    const next: NetState = {
      reachable,
      downtime,
      since: reachable ? null : this.firstFailureAt,
    };
    const changed =
      next.reachable !== this.state.reachable || next.downtime !== this.state.downtime;
    this.state = next;
    if (changed) for (const listener of this.listeners) listener(next);
  }
}

/** Ask the API whether it is there. `/health` is unauthenticated and cheap. */
export function healthProbe(apiBase: string): () => Promise<boolean> {
  return async () => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
    try {
      const res = await fetch(`${apiBase}/health`, {
        signal: controller.signal,
        // Never let a cache answer for the server: a cached 200 would report a
        // dead API as healthy, which is the one lie this must not tell.
        cache: "no-store",
      });
      return res.ok;
    } catch {
      return false;
    } finally {
      clearTimeout(timer);
    }
  };
}
