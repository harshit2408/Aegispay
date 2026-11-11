export function demoMode(value: unknown) {
  if (!value || typeof value !== "object" || !("demo_enabled" in value) || !("replay_mode" in value) ||
    typeof value.demo_enabled !== "boolean" || typeof value.replay_mode !== "boolean") {
    throw new Error("Demo status unavailable.");
  }
  return { enabled: value.demo_enabled, replayed: value.replay_mode };
}
