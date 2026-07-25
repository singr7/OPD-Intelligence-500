"use client";

// The two deferred panels (doc 03 §10). Protocol templates need S17's regimen
// families; slot templates need S15's appointment slot inventory. Rather than
// build an editor over a model that doesn't exist yet (that would be rework the
// moment S15/S17 settle the schema), the console shows an honest placeholder
// driven by the API's `deferred` marker — so the finished console's shape is
// visible without pretending the data is.

import type { TabProps } from "./Console";
import { useLoad } from "../_lib/useLoad";
import * as api from "../_lib/api";

export function ComingSoonTab({ token, onError }: TabProps) {
  const protocol = useLoad(() => api.fetchProtocolTemplates(token), onError);
  const slots = useLoad(() => api.fetchSlotTemplates(token), onError);

  return (
    <>
      <section>
        <h2>Protocol templates</h2>
        <div className="notice">
          <b>Arrives with {protocol.data?.arrives_in ?? "S17"}.</b>{" "}
          {protocol.data?.reason ??
            "Regimen-family protocol templates are built with the check-in engine."}
        </div>
      </section>
      <section>
        <h2>Slot templates</h2>
        <div className="notice">
          <b>Arrives with {slots.data?.arrives_in ?? "S15"}.</b>{" "}
          {slots.data?.reason ??
            "Slot templates need the appointment slot inventory (telephony part 2)."}
        </div>
      </section>
      <section>
        <h2>Downtime drill</h2>
        <div className="notice">
          Run the downtime drill from the <b>Coordinator</b> console — it owns the queue’s
          downtime state (one switch, one audit trail). Admin does not keep a second copy.
        </div>
      </section>
    </>
  );
}
