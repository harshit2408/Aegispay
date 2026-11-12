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
  const detail = (error instanceof Error ? error.message : "").replace(/\.+$/, "");
  // fetch rejects with TypeError when the request may or may not have reached the server.
  if (error instanceof TypeError) return "Network error: outcome unknown. Check the run id and case grid before retrying.";
  if (status === 504) return "Reset still pending: the owner reset runner has not finished. Press Reset run again to keep waiting.";
  if (status === 502) return "Reset failed in the owner reset runner. Check the runner log, then try again.";
  if (status === 503) return `Unavailable on this server: ${detail || "demo control is not configured"}.`;
  if (status === 429) return "Too many demo requests. Wait a minute, then try again.";
  if (status === 401) return "Session expired. Sign in again as the admin operator.";
  if (status === 403) return `Not permitted (${detail || "forbidden"}). Use the admin operator, or reload to refresh the session.`;
  return (error instanceof Error && error.message) || "Demo action could not be confirmed.";
}

export type DemoRunState = { pending: string; message: string; failure: string };
export const IDLE: DemoRunState = { pending: "", message: "", failure: "" };

/**
 * One demo action at a time. The guard lives outside the keyed toolbar, so a remount mid-request
 * cannot start a second one. Each click is exactly one attempt: 504 and other failures are reported,
 * never retried automatically (a retry could queue another reset or admit another case).
 */
export function createDemoRunner() {
  let pending = "";
  return {
    get pending() { return pending; },
    async run(label: string, action: () => Promise<string>, report: (state: DemoRunState) => void): Promise<void> {
      if (pending) return;
      pending = label;
      report({ ...IDLE, pending: label });
      try {
        const message = await action();
        pending = "";
        report({ ...IDLE, message });
      } catch (error) {
        pending = "";
        report({ ...IDLE, failure: demoFailure(error) });
      }
    },
  };
}

/** aria-pressed for a detector button: true only for the server-confirmed state. */
export function detectorPressed(control: { detector_enabled: boolean } | undefined, enabled: boolean): boolean {
  return control?.detector_enabled === enabled;
}

/** Control summary; "last confirmed" once a reset/toggle is pending or failed after it. */
export function controlSummary(control: { run_id: string; detector_enabled: boolean }, stale: boolean): string {
  return `Run ${control.run_id} · Detector ${control.detector_enabled ? "ON" : "OFF"} (${stale ? "last confirmed" : "server confirmed"})`;
}
