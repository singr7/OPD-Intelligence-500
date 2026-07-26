"use client";

// The admin console shell (doc 03 §10/§11, S18). A tab bar over the panels whose
// backing models exist today; the two deferred panels (protocol/slot) render an
// explicit "arrives with S15/S17" placeholder driven by the API's deferred marker,
// so the shape of the finished console is visible without pretending the data is.

import { useState } from "react";
import { AuthError } from "@/app/_lib/queue";
import { ADMIN_CSS } from "./adminStyles";
import { CostTab } from "./CostTab";
import { OpsTab } from "./OpsTab";
import { TreesTab } from "./TreesTab";
import { PriceBookTab } from "./PriceBookTab";
import { RegistryTab } from "./RegistryTab";
import { ComingSoonTab } from "./ComingSoonTab";

type TabId = "cost" | "ops" | "trees" | "prices" | "registry" | "soon";

const TABS: { id: TabId; label: string }[] = [
  { id: "cost", label: "Cost & tokens" },
  { id: "ops", label: "Operations" },
  { id: "trees", label: "Trees" },
  { id: "prices", label: "Price book" },
  { id: "registry", label: "Templates & voice" },
  { id: "soon", label: "Protocols & slots" },
];

export function Console({ token, onSignOut }: { token: string; onSignOut: () => void }) {
  const [tab, setTab] = useState<TabId>("cost");

  // A 401 anywhere means the token expired mid-session; drop straight to login.
  const onError = (err: unknown) => {
    if (err instanceof AuthError) onSignOut();
  };

  return (
    <div className="admin">
      <style dangerouslySetInnerHTML={{ __html: ADMIN_CSS }} />
      <header>
        <span className="badge">Admin</span>
        <h1>OPD control room</h1>
        <span className="spacer" />
        <button onClick={onSignOut}>Sign out</button>
      </header>
      <nav>
        {TABS.map((t) => (
          <button
            key={t.id}
            className={t.id === tab ? "active" : ""}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <main>
        {tab === "cost" && <CostTab token={token} onError={onError} />}
        {tab === "ops" && <OpsTab token={token} onError={onError} />}
        {tab === "trees" && <TreesTab token={token} onError={onError} />}
        {tab === "prices" && <PriceBookTab token={token} onError={onError} />}
        {tab === "registry" && <RegistryTab token={token} onError={onError} />}
        {tab === "soon" && <ComingSoonTab token={token} onError={onError} />}
      </main>
    </div>
  );
}

export type TabProps = { token: string; onError: (err: unknown) => void };
