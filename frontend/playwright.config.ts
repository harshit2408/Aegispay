import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://localhost:3100",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
    channel: process.env.E2E_BROWSER_CHANNEL,
    video: process.env.E2E_RECORD ? { mode: "on", size: { width: 1280, height: 720 } } : "off",
  },
  webServer: [
    ...(process.env.E2E_API_EXTERNAL ? [] : [{
      command: `${process.env.E2E_PYTHON ?? "python"} e2e/replay_server.py`,
      url: "http://localhost:8100/api/health",
      timeout: 120_000,
      reuseExistingServer: false,
    }]),
    {
      command: "npm run dev -- --hostname localhost --port 3100",
      url: "http://localhost:3100",
      timeout: 120_000,
      reuseExistingServer: false,
      env: { NEXT_PUBLIC_API_URL: "http://localhost:8100", NEXT_PUBLIC_AG_LICENSE_KEY: "" },
    },
  ],
});
