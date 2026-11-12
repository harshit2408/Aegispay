import { test, expect, type APIRequestContext } from "@playwright/test";

const apiURL = "http://localhost:8100";
const password = "browser-replay-fixture-password";

async function login(client: APIRequestContext, operator: string) {
  const response = await client.post(`${apiURL}/api/auth/login`, {
    data: { operator_id: `demo-${operator}`, password },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()).csrf_token as string;
}

test("S1–S4 replay uses real authenticated routes, graph decisions and browser case views", async ({ page, playwright }) => {
  const admin = await playwright.request.newContext();
  const controller = await playwright.request.newContext();
  try {
    const adminCSRF = await login(admin, "admin");
    const controllerCSRF = await login(controller, "controller1");
    const reset = await admin.post(`${apiURL}/api/demo/reset`, { headers: { "X-CSRF-Token": adminCSRF } });
    expect(reset.ok(), await reset.text()).toBeTruthy();
    expect((await reset.json()).replayed).toBe(true);

    const health = await admin.get(`${apiURL}/api/health`);
    expect(health.ok(), await health.text()).toBeTruthy();
    expect(await health.json()).toMatchObject({ demo_enabled: true, replay_mode: true });

    await page.goto("/");
    // Persistent global REPLAY indicator, driven by the health flag (R24, PRD §16 Honesty).
    await expect(page.locator(".replay-banner").filter({ hasText: /^REPLAY · / }).first()).toBeVisible();
    await page.getByText("Operator sign in", { exact: true }).click();
    await page.getByLabel("Operator ID", { exact: true }).fill("demo-cfo");
    await page.getByLabel("Password", { exact: true }).fill(password);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();

    const executions: Record<string, string> = {};
    for (const [scenario, route, payable] of [
      ["S1", "AUTO_SETTLE", "$480.00"],
      ["S2", "SHORT_PAY", "$425.00"],
      ["S3", "FREEZE", "$400.00"],
    ]) {
      const response = await controller.post(`${apiURL}/api/demo/scenarios/${scenario}/run`, {
        headers: { "X-CSRF-Token": controllerCSRF },
      });
      expect(response.status(), await response.text()).toBe(202);
      const receipt = await response.json();
      executions[scenario] = receipt.execution_id;
      await page.goto(`/cases/${receipt.execution_id}`);
      await expect(page.getByText("REPLAY · Recorded case data", { exact: true })).toBeVisible();
      await expect(page.getByRole("heading", { name: `Gate · ${route}`, exact: true })).toBeVisible();
      await expect(page.getByRole("heading", { name: "Payee source: vendor master (locked)", exact: true })).toBeVisible();
      await expect(page.getByText("Gate payable · locked", { exact: true }).locator("..")).toContainText(payable);
      if (process.env.E2E_RECORD) await page.waitForTimeout(4000);
      if (scenario === "S2") {
        await expect(page.getByText(/Rejected portion:/)).toContainText("$75.00");
        await expect(page.getByRole("row", { name: "Pay and claim $65.92" })).toBeVisible();
        await expect(page.getByRole("row", { name: "Short-pay + notice $15.00" })).toBeVisible();
      }
      if (scenario === "S3") {
        await expect(page.getByText("HIGH_RISK", { exact: true })).toBeVisible();
        await page.getByLabel("Gate-allowed action").selectOption("REJECT");
        await expect(page.getByRole("button", { name: "Confirm reject", exact: true })).toBeDisabled();
        await page.getByLabel("Reason · at least 10 characters").fill("Rejected forged invoice evidence");
        await page.getByRole("button", { name: "Confirm reject", exact: true }).click();
        await expect(page.locator(".case-badge").filter({ hasText: /REJECTED/ })).toBeVisible();
      }
    }

    const duplicate = await controller.post(`${apiURL}/api/demo/scenarios/S4/run`, {
      headers: { "X-CSRF-Token": controllerCSRF },
    });
    expect(duplicate.status(), await duplicate.text()).toBe(202);
    const receipt = await duplicate.json();
    expect(receipt.duplicate_of_execution_id).toBe(executions.S1);
    await page.goto(`/cases/${receipt.execution_id}`);
    await expect(page.locator(".case-badge").filter({ hasText: /DUPLICATE_BLOCKED/ })).toBeVisible();
    await expect(page.getByRole("link", { name: "Open original execution", exact: true }))
      .toHaveAttribute("href", `/cases/${executions.S1}`);
    await expect(page.getByRole("button", { name: /^Confirm / })).toHaveCount(0);

    const disabled = await admin.post(`${apiURL}/api/demo/forensics-detector`, {
      headers: { "X-CSRF-Token": adminCSRF }, data: { enabled: false },
    });
    expect(disabled.ok(), await disabled.text()).toBeTruthy();
    const fallback = await controller.post(`${apiURL}/api/demo/scenarios/S3/run`, {
      headers: { "X-CSRF-Token": controllerCSRF },
    });
    expect(fallback.status(), await fallback.text()).toBe(202);
    await page.goto(`/cases/${(await fallback.json()).execution_id}`);
    await expect(page.getByRole("heading", { name: "Gate · FREEZE", exact: true })).toBeVisible();
    await expect(page.getByText("UNVERIFIED_SOURCE", { exact: true })).toBeVisible();
    const invariants = await admin.get(`${apiURL}/__test__/replay-invariants`);
    expect(invariants.ok(), await invariants.text()).toBeTruthy();
    expect(await invariants.json()).toEqual({ spend: 0, po: 0, outbox: 0 });
  } finally {
    await admin.dispose();
    await controller.dispose();
  }
});
