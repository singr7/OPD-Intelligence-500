"use client";

// The admin console shell (doc 03 §10/§11, S18). A tab bar over the editors and
// the analytics. As of S-GL.2 no panel is a placeholder any more — People was the
// last one, and it carried the console's only remaining deferral marker.

import { useState } from "react";
import {
  Activity,
  AudioLines,
  BookOpenCheck,
  Building2,
  Cable,
  ChevronRight,
  CircleDollarSign,
  ClipboardList,
  Network,
  Settings2,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { AuthError } from "@/app/_lib/queue";
import { ADMIN_CSS } from "./adminStyles";
import { ChannelsTab } from "./ChannelsTab";
import { FacilityTab } from "./FacilityTab";
import { PeopleTab } from "./PeopleTab";
import { CostTab } from "./CostTab";
import { OpsTab } from "./OpsTab";
import { TreesTab } from "./TreesTab";
import { PriceBookTab } from "./PriceBookTab";
import { RegistryTab } from "./RegistryTab";
import { ProtocolsTab } from "./ProtocolsTab";

type TabId =
  | "channels"
  | "facility"
  | "people"
  | "cost"
  | "ops"
  | "trees"
  | "prices"
  | "registry"
  | "protocols";

// Channels leads (S-GL.1): it is the tab that answers "can a patient reach us at
// all", which outranks every question the others answer. People is second
// (S-GL.2) for the same reason one rung down — a hospital with no doctor on the
// roster has an open channel and nobody to send anyone to.
const GROUPS: { label: string; items: { id: TabId; label: string; icon: LucideIcon }[] }[] = [
  {
    label: "Operations",
    items: [
      { id: "channels", label: "Channels", icon: Cable },
      { id: "ops", label: "System operations", icon: Activity },
    ],
  },
  // The facility sits between "can a patient reach us" and "is there anybody to
  // send them to": what this hospital is called, and which of its departments
  // are open. Doc 24's second system of medicine arrives here as a department
  // an administrator marks ayurveda.
  {
    label: "Facility",
    items: [{ id: "facility", label: "Hospital and departments", icon: Building2 }],
  },
  {
    label: "Workforce",
    items: [{ id: "people", label: "People and roster", icon: Users }],
  },
  {
    label: "Clinical content",
    items: [
      { id: "trees", label: "Intake trees", icon: Network },
      { id: "protocols", label: "Protocols and slots", icon: BookOpenCheck },
      { id: "registry", label: "Templates and voice", icon: AudioLines },
    ],
  },
  {
    label: "Finance and control",
    items: [
      { id: "cost", label: "Cost and tokens", icon: CircleDollarSign },
      { id: "prices", label: "Price book", icon: ClipboardList },
    ],
  },
];

export function Console({ token, onSignOut }: { token: string; onSignOut: () => void }) {
  const [tab, setTab] = useState<TabId>("channels");

  // A 401 anywhere means the token expired mid-session; drop straight to login.
  const onError = (err: unknown) => {
    if (err instanceof AuthError) onSignOut();
  };

  return (
    <div className="admin">
      <style dangerouslySetInnerHTML={{ __html: ADMIN_CSS }} />
      <header className="admin-topbar">
        <span className="admin-mark"><Settings2 aria-hidden="true" /></span>
        <span className="admin-title">
          <h1>OPD control room</h1>
          <small>Administration</small>
        </span>
        <span className="spacer" />
        <button onClick={onSignOut}>Sign out</button>
      </header>
      <div className="admin-body">
        <nav className="admin-nav" aria-label="Administration sections">
          {GROUPS.map((group) => (
            <section key={group.label}>
              <h2>{group.label}</h2>
              {group.items.map(({ icon: Icon, ...item }) => (
                <button
                  key={item.id}
                  className={item.id === tab ? "active" : ""}
                  onClick={() => setTab(item.id)}
                >
                  <Icon aria-hidden="true" />
                  <span>{item.label}</span>
                  <ChevronRight className="chevron" aria-hidden="true" />
                </button>
              ))}
            </section>
          ))}
        </nav>
        <main>
          {tab === "channels" && <ChannelsTab token={token} onError={onError} />}
          {tab === "facility" && <FacilityTab token={token} onError={onError} />}
          {tab === "people" && <PeopleTab token={token} onError={onError} />}
          {tab === "cost" && <CostTab token={token} onError={onError} />}
          {tab === "ops" && <OpsTab token={token} onError={onError} />}
          {tab === "trees" && <TreesTab token={token} onError={onError} />}
          {tab === "prices" && <PriceBookTab token={token} onError={onError} />}
          {tab === "registry" && <RegistryTab token={token} onError={onError} />}
          {tab === "protocols" && <ProtocolsTab token={token} onError={onError} />}
        </main>
      </div>
    </div>
  );
}

export type TabProps = { token: string; onError: (err: unknown) => void };
