// Health endpoint for the compose healthcheck + uptime-kuma probing the web PWA.
import { NextResponse } from "next/server";

export const dynamic = "force-static";

export function GET() {
  return NextResponse.json({ status: "ok", service: "web", version: "0.1.0" });
}
