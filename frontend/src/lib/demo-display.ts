export function demoMode(value: unknown) {
  if (!value || typeof value !== "object" || !("demo_enabled" in value) || !("replay_mode" in value) ||
    typeof value.demo_enabled !== "boolean" || typeof value.replay_mode !== "boolean") {
    throw new Error("Demo status unavailable.");
  }
  return { enabled: value.demo_enabled, replayed: value.replay_mode };
}

/** PRD §14.5: the toolbar (reset, S1–S4, detector) renders only for admin and only when DEMO_ENABLED. */
export function showsDemoToolbar(mode: { enabled: boolean } | undefined, role: string | undefined): boolean {
  return mode?.enabled === true && role === "admin";
}

/** User-facing text for a failed demo control call; statuses per PRD §13 and docs/DEMO_SETUP.md live reset. */
export function demoFailure(error: unknown): string {
  const status = typeof error === "object" && error !== null && "status" in error ? error.status : undefined;
  const detail = error instanceof Error ? error.message : "";
  if (status === 504) return "Reset still pending: the owner reset runner has not finished. Press Reset run again to keep waiting.";
  if (status === 502) return "Reset failed in the owner reset runner. Check the runner log, then try again.";
  if (status === 503) return `Unavailable on this server: ${detail || "demo control is not configured"}.`;
  if (status === 429) return "Too many demo requests. Wait a minute, then try again.";
  if (status === 403) return "This session cannot use demo controls. Sign in as the admin operator.";
  return detail || "Demo action could not be confirmed.";
}
