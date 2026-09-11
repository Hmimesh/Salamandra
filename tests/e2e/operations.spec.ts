import { expect, test as base, type Page, type Locator } from "@playwright/test";
import { execFileSync } from "node:child_process";

const test = base.extend<{ errorGuard: void }>({
  errorGuard: [async ({ page }, use, testInfo) => {
    const errors = watchErrors(page);
    let active = 0, peak = 0, sent = 0;
    const failures: { path: string; error: string | null }[] = [];
    page.on("request", () => { sent++; active++; peak = Math.max(peak, active); });
    page.on("requestfinished", () => { active--; });
    page.on("requestfailed", request => { active--; failures.push({ path: new URL(request.url()).pathname, error: request.failure()?.errorText || null }); });
    await use();
    if (errors.length || testInfo.status !== testInfo.expectedStatus) {
      console.log("QA network summary", JSON.stringify({ sent, peak, active, failures }));
      await testInfo.attach("network-summary", { body: JSON.stringify({ sent, peak, active, failures }), contentType: "application/json" });
      if (process.platform === "win32" && errors.some(error => error.includes("ERR_NO_BUFFER_SPACE"))) {
        try {
          const output = execFileSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command",
            "Get-NetTCPConnection | Group-Object State | Select-Object Name,Count; netsh int ipv4 show dynamicport tcp; Get-Process chrome,python -ErrorAction SilentlyContinue | Select-Object ProcessName,Handles,WorkingSet64"], { timeout: 10000, windowsHide: true });
          console.log("QA Windows socket diagnostics", output.toString("utf8"));
          await testInfo.attach("windows-socket-diagnostics", { body: output, contentType: "text/plain" });
        } catch (error) {
          await testInfo.attach("windows-socket-diagnostics", { body: String(error), contentType: "text/plain" });
        }
      }
    }
    const expected = testInfo.annotations.filter(annotation => annotation.type === "expected-console-error").map(annotation => annotation.description);
    expect(errors).toEqual(expected);
  }, { auto: true }],
});

async function activate(control: Locator) {
  await control.focus();
  await expect(control).toBeFocused();
  await control.press("Enter");
}

async function savePreference(page: Page, action: () => Promise<unknown>) {
  await Promise.all([
    page.waitForResponse(response => response.url().endsWith("/api/account/preferences") && response.status() === 200),
    action(),
  ]);
}


async function signIn(page: Page, email = "owner@playwright.test") {
  await page.goto("/login");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill("playwright-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("main").getByRole("heading").first()).toBeVisible();
  await page.goto("/settings");
  // A database-backed fixture exposes overlap: finish each reset before sending the next.
  await Promise.all([
    page.waitForResponse(response => response.url().endsWith("/api/account/preferences") && response.status() === 200),
    activate(page.getByRole("button", { name: "light", exact: true })),
  ]);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await Promise.all([
    page.waitForResponse(response => response.url().endsWith("/api/account/preferences") && response.status() === 200),
    page.locator(".preference-row").filter({ hasText: "Text size" }).getByRole("combobox").selectOption("comfortable"),
  ]);
  await expect(page.locator("html")).toHaveAttribute("data-font-scale", "comfortable");
  await savePreference(page, () => activate(page.getByRole("button", { name: "Comfortable", exact: true })));
  await expect(page.locator("html")).toHaveAttribute("data-font-scale", "comfortable");
  await expect(page.locator("html")).toHaveAttribute("data-density", "comfortable");
}

function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(`page: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  return errors;
}

async function expectNoViewportOverflow(page: Page) {
  const sizes = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(sizes.scroll, `horizontal overflow: ${JSON.stringify(sizes)}`).toBeLessThanOrEqual(sizes.client + 1);
}

test("Phase D history suggestions remain operator controlled", async ({ page }, testInfo) => {
  await signIn(page, `owner@phase-d-${testInfo.project.name}.test`);
  await savePreference(page, () => activate(page.getByRole("button", { name: "dark", exact: true })));
  await savePreference(page, () => page.locator(".preference-row").filter({ hasText: "Text size" }).getByRole("combobox").selectOption("largest"));
  await page.goto("/events/new");
  await expect(page).toHaveTitle(/Salamandra/i);
  const requests: string[] = [];
  page.on("request", request => { if (request.method() === "POST") requests.push(new URL(request.url()).pathname); });
  await activate(page.getByRole("button", { name: "Build manually" }));
  const form = page.locator("form.brief-editor");
  await form.getByLabel("Event name").fill("אירוע عربي, mixed!");
  await form.getByLabel("Date", { exact: true }).fill("2026-11-24");
  await form.getByLabel("Start time").fill("19:00");
  await form.getByLabel("Duration (minutes)").fill("120");
  await form.getByLabel("Guests").fill("100");
  await form.getByLabel("Department & item").selectOption("furniture.chair");
  await activate(page.getByRole("button", { name: "Build event plan" }));
  const history = page.getByRole("region", { name: "From your event history" });
  await expect(history.getByText("Based on 3 similar past events", { exact: true })).toBeVisible();
  const quantity = history.getByRole("spinbutton", { name: "Suggested quantity for furniture.chair" });
  await expect(quantity).toHaveValue("12");
  await expectNoViewportOverflow(page);
  const clipped = await page.locator(".event-composer").evaluate(root => {
    const boundary = root.getBoundingClientRect();
    return [...root.querySelectorAll("button, input, select")].filter(control => {
      const rect = control.getBoundingClientRect();
      return rect.width > 0 && (rect.left < boundary.left - 1 || rect.right > boundary.right + 1);
    }).map(control => control.getAttribute("aria-label") || control.textContent);
  });
  expect(clipped, "planner controls must not be hidden by ancestor clipping").toEqual([]);
  await activate(history.getByRole("button", { name: "Ignore", exact: true }));
  await expect(history.getByRole("button", { name: "Review suggestions" })).toBeFocused();
  await expect(form.getByLabel("Quantity", { exact: true })).toHaveValue("1");
  await activate(history.getByRole("button", { name: "Review suggestions" }));
  await expect(quantity).toBeFocused();
  await quantity.fill("13");
  await page.keyboard.press("Tab");
  await expect(history.getByRole("button", { name: "Ignore", exact: true })).toBeFocused();
  expect(await history.getByRole("button", { name: "Ignore", exact: true }).evaluate(el => getComputedStyle(el).outlineStyle)).not.toBe("none");
  await page.keyboard.press("Tab");
  await expect(history.getByRole("button", { name: "Apply to manual plan" })).toBeFocused();
  await page.screenshot({ path: testInfo.outputPath("history-dark-largest.png"), fullPage: true });
  await page.keyboard.press("Enter");
  await expect(form.getByLabel("Quantity", { exact: true })).toHaveValue("13");
  await expect(form.getByLabel("Quantity", { exact: true })).toBeFocused();
  const save = page.getByRole("button", { name: /Save as planning|Save event/ });
  await expect(save).toBeDisabled();
  expect(requests).toEqual(["/api/events/describe", "/api/events/learning/suggestions"]);
  const recalculated = page.waitForResponse(response => response.url().endsWith("/api/events/describe") && response.status() === 200);
  await activate(page.getByRole("button", { name: /Recalculate/ }));
  const draft = await (await recalculated).json();
  expect(draft.draft.event.capability_requirements).toContainEqual(expect.objectContaining({ capability: "furniture.chair", amount: 13 }));
  await expect(save).toBeEnabled();
  await expectNoViewportOverflow(page);
  const saved = page.waitForResponse(response => response.url().endsWith("/api/events/save") && response.status() === 201);
  await activate(save);
  const stored = await (await saved).json();
  expect(stored.event.title).toBe("אירוע عربي, mixed!");
  expect(stored.event.capability_requirements).toContainEqual(expect.objectContaining({ capability: "furniture.chair", amount: 13 }));
  expect(requests.some(path => /inventory|status|feedback|eligibility/.test(path))).toBe(false);
  await expect(page).toHaveURL(/\/events$/);
});

test("history states reject stale drafts and in-flight plans", async ({ page }, testInfo) => {
  await signIn(page, `owner@phase-d-${testInfo.project.name}.test`);
  await page.goto("/events/new");
  await activate(page.getByRole("button", { name: "Build manually" }));
  const form = page.locator("form.brief-editor");
  await form.getByLabel("Event name").fill("טיוטה عربي!");
  await form.getByLabel("Date", { exact: true }).fill("2026-12-14");
  await form.getByLabel("Start time").fill("19:00");
  await form.getByLabel("Guests").fill("100");
  await form.getByLabel("Duration (minutes)").fill("120");
  await form.getByLabel("Department & item").selectOption("furniture.chair");
  let releaseHistory!: () => void;
  const historyGate = new Promise<void>(resolve => { releaseHistory = resolve; });
  await page.route("**/api/events/learning/suggestions", async route => { await historyGate; await route.continue(); });
  await activate(page.getByRole("button", { name: "Build event plan" }));
  const history = page.getByRole("region", { name: "From your event history" });
  await expect(history.getByRole("status")).toHaveText("Checking completed events...");
  releaseHistory();
  await expect(history.getByRole("button", { name: "Apply to manual plan" })).toBeVisible();
  await activate(form.getByRole("button", { name: "Add", exact: true }));
  await expect(history).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Save as planning|Save event/ })).toBeDisabled();
  await activate(form.getByRole("button", { name: "Remove requirement" }).last());
  await form.getByLabel("Event name").fill("שם חדש عربي!");
  let releasePlan!: () => void;
  const planGate = new Promise<void>(resolve => { releasePlan = resolve; });
  await page.route("**/api/events/describe", async route => {
    const response = await route.fetch();
    await planGate;
    await route.fulfill({ response });
  });
  const planningRequest = page.waitForRequest("**/api/events/describe");
  await activate(page.getByRole("button", { name: /Recalculate/ }));
  await planningRequest;
  await form.getByLabel("Quantity", { exact: true }).fill("17");
  const staleResponse = page.waitForResponse("**/api/events/describe");
  releasePlan();
  await staleResponse;
  await expect(page.getByRole("button", { name: /Recalculate/ })).toBeEnabled();
  await expect(history).toHaveCount(0);
  await expect(form.getByLabel("Quantity", { exact: true })).toHaveValue("17");
  await expect(page.getByRole("button", { name: /Save as planning|Save event/ })).toBeDisabled();
  await page.unroute("**/api/events/describe");
  const recalculated = page.waitForResponse("**/api/events/describe");
  await activate(page.getByRole("button", { name: /Recalculate/ }));
  const updated = await (await recalculated).json();
  expect(updated.draft.event.title).toBe("שם חדש عربي!");
  expect(updated.draft.event.capability_requirements).toContainEqual(expect.objectContaining({ capability: "furniture.chair", amount: 17 }));
  await expect(history.getByRole("button", { name: "Apply to manual plan" })).toBeVisible();
  const applicationRequests: string[] = [];
  const collectApplication = (request: import("@playwright/test").Request) => {
    if (request.method() === "POST") applicationRequests.push(new URL(request.url()).pathname);
  };
  page.on("request", collectApplication);
  await history.getByRole("spinbutton").fill("18");
  await history.getByRole("button", { name: "Apply to manual plan" }).evaluate(button => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
  });
  await expect(form.getByLabel("Quantity", { exact: true })).toHaveValue("18");
  await expect(history).toHaveCount(0);
  expect(applicationRequests).toEqual([]);
  page.off("request", collectApplication);
  const appliedPlan = page.waitForResponse("**/api/events/describe");
  await activate(page.getByRole("button", { name: /Recalculate/ }));
  await appliedPlan;
  await expect(page.getByRole("button", { name: /Save as planning|Save event/ })).toBeEnabled();
  await expectNoViewportOverflow(page);
  const savedResponse = page.waitForResponse(response => response.url().endsWith("/api/events/save") && response.status() === 201);
  await activate(page.getByRole("button", { name: /Save as planning|Save event/ }));
  const saved = (await (await savedResponse).json()).event;
  await page.goto(`/events?event=${saved.id}`);
  await activate(page.getByRole("button", { name: "Edit event", exact: true }));
  const dialog = page.getByRole("dialog", { name: "Edit event", exact: true });
  const newer = await page.request.post("/api/events/update", { data: {
    event_id: saved.id, version: saved.version, description: saved.description,
    overrides: { title: "Newer operator title", planning_mode: "manual", capability_requirements: saved.capability_requirements },
  } });
  expect(newer.status()).toBe(200);
  await dialog.getByLabel("Event name").fill("Stale operator edit");
  testInfo.annotations.push({ type: "expected-console-error", description: "console: Failed to load resource: the server responded with a status of 409 (Conflict)" });
  const conflict = page.waitForResponse(response => response.url().endsWith("/api/events/update") && response.status() === 409);
  await activate(dialog.getByRole("button", { name: "Save and rebuild plan" }));
  const rejected = await (await conflict).json();
  await expect(page.getByText(rejected.error, { exact: true })).toBeVisible();
  await expect(dialog.getByLabel("Event name")).toHaveValue("Stale operator edit");
  const state = await (await page.request.get("/api/state")).json();
  expect(state.events.events.find((event: { id: string }) => event.id === saved.id).title).toBe("Newer operator title");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
});

test("history empty, insufficient and error states remain usable", async ({ page }, testInfo) => {
  await signIn(page);
  await page.goto("/events/new");
  await page.getByLabel("Event brief").fill("Small indoor meeting for 100 guests on 2026-12-20 at 18:00.");
  let mode = 0;
  await page.route("**/api/events/learning/suggestions", route => route.fulfill({
    status: mode === 2 ? 503 : 200, contentType: "application/json",
    body: JSON.stringify(mode === 2 ? { error: "History is temporarily unavailable." } : { suggestions: [], evidence_count: mode, warnings: [], dimensions: [] }),
  }));
  for (mode = 0; mode < 3; mode++) {
    if (mode === 2) testInfo.annotations.push({ type: "expected-console-error", description: "console: Failed to load resource: the server responded with a status of 503 (Service Unavailable)" });
    await activate(page.getByRole("button", { name: "Build event plan" }));
    const history = page.getByRole("region", { name: "From your event history" });
    if (mode === 2) await expect(history.getByRole("status")).toHaveText("History is temporarily unavailable.");
    else {
      await expect(history).toContainText("No similar completed events with repeated quantity evidence yet.");
      await expect(history.getByRole("button", { name: "Apply to manual plan" })).toHaveCount(0);
      await activate(history.getByRole("button", { name: "Ignore", exact: true }));
      await expect(history.getByRole("button", { name: "Review suggestions" })).toBeFocused();
      await activate(history.getByRole("button", { name: "Review suggestions" }));
      await expect(history.getByRole("button", { name: "Ignore", exact: true })).toBeFocused();
    }
    await activate(page.getByRole("button", { name: "Revise brief" }));
  }
  await expectNoViewportOverflow(page);
});

test("major operator routes remain responsive and error free", async ({ page }) => {
  const errors = watchErrors(page);
  await signIn(page);
  for (const [path, heading] of [
    ["/events", "Events"],
    ["/inventory", "Inventory"],
    ["/kits", "Kits & templates"],
    ["/returns", "Returns & checklists"],
    ["/team", "Team"],
    ["/activity", "Activity"],
    ["/settings", "Settings"],
  ] as const) {
    const menu = page.getByRole("button", { name: "Open navigation", exact: true });
    if (await menu.isVisible()) await activate(menu);
    await activate(page.getByRole("navigation", { name: "Main navigation" }).locator(`a[href="${path}"]`));
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    await expectNoViewportOverflow(page);
  }
  expect(errors).toEqual([]);
});

test("dialogs trap focus, restore focus, and close with Escape", async ({ page }) => {
  await signIn(page);
  await page.goto("/inventory");
  const trigger = page.getByRole("button", { name: "Add item" });
  await activate(trigger);
  const dialog = page.getByRole("dialog", { name: "Add inventory" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Close dialog" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "Create a custom item" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Close dialog" })).toBeFocused();
  expect(await dialog.getByRole("button", { name: "Close dialog" }).evaluate((el) => getComputedStyle(el).outlineStyle)).not.toBe("none");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("custom inventory and dependencies can be created", async ({ page }, testInfo) => {
  const errors = watchErrors(page);
  await signIn(page);
  await page.goto("/inventory");
  await page.getByRole("button", { name: "Add item" }).click();
  await page.getByRole("button", { name: "Create a custom item" }).click();
  const dialog = page.getByRole("dialog", { name: "Add custom item" });
  const name = `Qa ${testInfo.project.name} microphone`;
  await dialog.getByLabel("Item name").fill(name);
  await dialog.getByLabel("Category").selectOption("microphone");
  await dialog.getByLabel("Linked inventory item").selectOption("xlr cable");
  await dialog.getByLabel("Minimum quantity").fill("2");
  await expectNoViewportOverflow(page);
  await activate(dialog.getByRole("button", { name: "Add equipment" }));
  await page.getByPlaceholder("Search inventory").fill(name);
  await expect(page.getByText("2x Xlr Cable", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /Check out QA vocal microphone/i })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("kit creation and manual planning remain usable", async ({ page }, testInfo) => {
  const errors = watchErrors(page);
  await signIn(page);
  await page.goto("/kits");
  await page.getByRole("button", { name: "Create kit" }).click();
  const kitDialog = page.getByRole("dialog", { name: "Create equipment kit" });
  const kitName = `QA ${testInfo.project.name} kit`;
  await kitDialog.getByLabel("Kit name").fill(kitName);
  await kitDialog.getByLabel("Inventory item").selectOption("main speaker");
  await expectNoViewportOverflow(page);
  await activate(kitDialog.getByRole("button", { name: "Save kit" }));
  await expect(page.getByRole("heading", { name: kitName, exact: true })).toBeVisible();

  await page.goto("/events/new");
  await activate(page.getByRole("button", { name: "Build manually" }));
  await page.getByLabel("Event name").fill("QA manual event");
  await page.getByLabel("Date").fill("2026-09-24");
  await page.getByLabel("Start time").fill("19:00");
  await page.getByLabel("Venue").fill("QA Hall");
  await expectNoViewportOverflow(page);
  await activate(page.getByRole("button", { name: "Build event plan" }));
  await expect(page.getByText("Operations plan")).toBeVisible();
  await expect(page.getByRole("heading", { name: "QA manual event" })).toBeVisible();
  const teammate = page.getByRole("checkbox", { name: /Playwright Technician/ });
  await teammate.focus();
  await teammate.press("Space");
  await expect(teammate).toBeChecked();
  await expectNoViewportOverflow(page);
  expect(errors).toEqual([]);
});

test("preset inventory and account popovers work with a keyboard", async ({ page }) => {
  await signIn(page);
  const account = page.locator(".account-trigger");
  await activate(account);
  await expect(page.getByRole("menu")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).toBeHidden();
  await expect(account).toBeFocused();
  await page.goto("/inventory");
  await activate(page.getByRole("button", { name: "Add item", exact: true }));
  await activate(page.getByRole("button", { name: "Start from a preset" }));
  const catalog = page.getByRole("dialog", { name: "Item catalog" });
  await Promise.all([
    page.waitForResponse(response => response.url().endsWith("/api/inventory/presets") && response.status() === 200),
    activate(catalog.getByRole("button", { name: "Add XLR Cable 10m", exact: true })),
  ]);
  await activate(catalog.getByRole("button", { name: "Done", exact: true }));
  await page.getByPlaceholder("Search inventory").fill("XLR Cable 10m");
  await expect(page.locator(".inventory-table tbody tr")).toHaveCount(1);
  await expectNoViewportOverflow(page);
  await activate(account);
  await activate(page.getByRole("menuitem", { name: "Sign out", exact: true }));
  await expect(page.getByRole("link", { name: "Sign in", exact: true }).first()).toBeVisible();
  await signIn(page, "tech@playwright.test");
  await page.goto("/inventory");
  await activate(page.getByRole("button", { name: "Add item", exact: true }));
  await activate(page.getByRole("button", { name: "Start from a preset" }));
  await expect(catalog.getByText("Add proven presets to your inventory.")).toBeVisible();
  await Promise.all([
    page.waitForResponse(response => response.url().endsWith("/api/inventory/presets") && response.status() === 200),
    activate(catalog.getByRole("button", { name: "Add XLR Cable 10m", exact: true })),
  ]);
  await activate(catalog.getByRole("button", { name: "Done", exact: true }));
  await activate(page.getByRole("button", { name: "My inventory", exact: true }));
  await page.getByPlaceholder("Search inventory").fill("XLR Cable 10m");
  await expect(page.locator(".inventory-table tbody tr")).toHaveCount(1);
});

test("calendar overflow and destructive confirmations preserve keyboard focus", async ({ page }, testInfo) => {
  await signIn(page);
  const today = new Date();
  const date = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-22`;
  for (let index = 0; index < 3; index++) {
    const response = await page.request.post("/api/events/save", {
      headers: { Origin: "http://127.0.0.1:4173" },
      data: {
        description: `Small meeting on ${date} at 19:00 with no lighting.`,
        overrides: { title: `Calendar ${testInfo.project.name} ${index}`, start_date: date },
        idempotency_key: `calendar-${testInfo.project.name}-${index}`,
      },
    });
    expect(response.status()).toBe(201);
  }
  await page.goto("/events");
  const more = page.getByRole("button", { name: /Show .* more events on/ }).first();
  await activate(more);
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(more).toBeFocused();
  await activate(page.locator(".calendar-event").first());
  const detail = page.getByRole("dialog").first();
  for (const [action, title] of [["Cancel event", "Cancel this event?"], ["Delete draft", "Delete this draft?"]] as const) {
    const trigger = detail.getByRole("button", { name: action, exact: true });
    await activate(trigger);
    const confirmation = page.getByRole("dialog", { name: title, exact: true });
    await expect(confirmation).toBeVisible();
    await page.keyboard.press("Shift+Tab");
    await expect(confirmation.getByRole("button").last()).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(confirmation).toBeHidden();
    await expect(trigger).toBeFocused();
  }
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await expectNoViewportOverflow(page);
});

test("operational text scales in both themes and at 200 percent zoom", async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  await signIn(page);
  for (const theme of ["light", "dark"] as const) {
    for (const [scale, pixels] of [["comfortable", 14], ["large", 15.75], ["largest", 17.5]] as const) {
      await page.goto("/settings");
      await savePreference(page, () => activate(page.getByRole("button", { name: theme, exact: true })));
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await savePreference(page, () => page.locator(".preference-row").filter({ hasText: "Text size" }).getByRole("combobox").selectOption(scale));
      await expect(page.locator("html")).toHaveAttribute("data-font-scale", scale);
      await page.goto("/events/new");
      await activate(page.getByRole("button", { name: "Build manually" }));
      await expect(page.locator(".manual-requirement-row > label").first()).toHaveCSS("font-size", `${pixels}px`);
      await expectNoViewportOverflow(page);
      await page.goto("/inventory");
      await activate(page.getByRole("button", { name: "Add item", exact: true }));
      await activate(page.getByRole("button", { name: "Create a custom item" }));
      await expect(page.locator(".dependency-row label").first()).toHaveCSS("font-size", `${pixels}px`);
      await expectNoViewportOverflow(page);
      if (scale === "largest") {
        await page.screenshot({ path: testInfo.outputPath(`${theme}-largest.png`), fullPage: true });
      }
      await page.keyboard.press("Escape");
    }
  }
  // The desktop-200-percent project applies browser-zoom-equivalent reflow and DPR.
  await page.goto("/events/new");
  await activate(page.getByRole("button", { name: "Build manually" }));
  await expectNoViewportOverflow(page);
  await page.getByLabel("Event name").fill("Zoom keyboard check");
  await page.screenshot({ path: testInfo.outputPath("manual-largest.png"), fullPage: true });
});

test("multilingual inventory display and RTL briefs preserve original text", async ({ page }, testInfo) => {
  await signIn(page);
  await page.goto("/inventory");
  await activate(page.getByRole("button", { name: "Add item", exact: true }));
  await activate(page.getByRole("button", { name: "Create a custom item" }));
  const dialog = page.getByRole("dialog", { name: "Add custom item" });
  const name = `רמקול — ميكروفون — CAFÉ 🎤 ${testInfo.project.name}`;
  await dialog.getByLabel("Item name").fill(name);
  await expect(dialog.getByLabel("Item name")).toHaveAttribute("dir", "auto");
  await expect(dialog.getByLabel("Item name")).toHaveCSS("direction", "rtl");
  await dialog.getByLabel("Category").selectOption("speaker");
  await activate(dialog.getByRole("button", { name: "Add equipment" }));
  await page.getByPlaceholder("Search inventory").fill(`cafe\u0301 🎤 ${testInfo.project.name}`);
  await expect(page.locator(".inventory-table tbody tr")).toHaveCount(1);
  await expect(page.getByText(name, { exact: true })).toBeVisible();
  await expect(page.getByText(name, { exact: true })).toHaveCSS("direction", "rtl");
  await activate(page.locator(".inventory-table tbody tr").getByRole("button", { name: /^Edit / }));
  const edit = page.getByRole("dialog", { name: "Edit equipment" });
  await expect(edit.getByLabel("Item name")).toHaveValue(name);
  await edit.getByLabel("Item name").fill(`${name} — geändert`);
  const response = page.waitForResponse((value) => value.url().endsWith("/api/inventory/items/update"));
  await activate(edit.getByRole("button", { name: "Save changes" }));
  const saved = await (await response).json();
  expect(saved.item.id).toBe(name.toLowerCase());
  expect(saved.item.display_name).toBe(`${name} — geändert`);
  expect(saved.item.canonical_type).toBe("speaker");
  await expectNoViewportOverflow(page);
  await page.goto("/events/new");
  const brief = page.getByLabel("Event brief", { exact: true });
  const description = "מופע בבית קפה — عرض موسيقي — Café 🎤";
  await brief.fill(description);
  await expect(brief).toHaveValue(description);
  await expect(brief).toHaveCSS("direction", "rtl");
  await expectNoViewportOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("multilingual-brief.png"), fullPage: true });
});

test("compact text retains the operator floor without losing dense layout", async ({ page }, testInfo) => {
  await signIn(page);
  const assertFloor = async (selector: string) => {
    const elements = page.locator(selector).filter({ visible: true });
    await expect(elements.first()).toBeVisible();
    expect(await elements.count()).toBeGreaterThan(0);
    for (const element of await elements.all()) {
      if (await element.isVisible()) {
        expect(await element.evaluate((el) => parseFloat(getComputedStyle(el).fontSize)), selector).toBeGreaterThanOrEqual(14);
      }
    }
  };
  const assertControls = async () => {
    await expectNoViewportOverflow(page);
    const dialog = page.getByRole("dialog").last();
    const surface = await dialog.isVisible() ? dialog : page.getByRole("main");
    const controls = surface.locator('input:not([type="hidden"]), select, button');
    for (const control of await controls.all()) {
      if (!(await control.isVisible())) continue;
      await control.scrollIntoViewIfNeeded();
      const bounds = await control.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(page.viewportSize()!.width + 1);
      expect(bounds!.y).toBeGreaterThanOrEqual(0);
      expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(page.viewportSize()!.height + 1);
      expect(await control.evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
        await control.evaluate((el) => el.outerHTML)).toBeTruthy();
    }
  };
  await page.locator(".preference-row").filter({ hasText: "Text size" }).getByRole("combobox").selectOption("compact");
  await expect(page.locator("html")).toHaveAttribute("data-font-scale", "compact");
  const comfortableGap = await page.locator(".settings-grid").evaluate((el) => parseFloat(getComputedStyle(el).gap));
  await savePreference(page, () => activate(page.getByRole("button", { name: "Compact", exact: true })));
  await expect(page.locator("html")).toHaveAttribute("data-density", "compact");
  const compactGap = await page.locator(".settings-grid").evaluate((el) => parseFloat(getComputedStyle(el).gap));
  expect(compactGap).toBeLessThanOrEqual(comfortableGap - 4);
  await page.goto("/inventory");
  await activate(page.getByRole("button", { name: "Add item", exact: true }));
  await activate(page.getByRole("button", { name: "Create a custom item" }));
  await assertFloor(".dependency-row label");
  await assertControls();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await page.goto("/kits");
  await activate(page.getByRole("button", { name: "Create kit", exact: true }));
  await assertFloor(".kit-builder-line label");
  await assertControls();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await page.goto("/events/new");
  await activate(page.getByRole("button", { name: "Build manually" }));
  await assertFloor(".manual-requirement-row > label");
  await assertControls();
  const date = new Date().toISOString().slice(0, 10);
  const response = await page.request.post("/api/events/save", {
    headers: { Origin: "http://127.0.0.1:4173" },
    data: {
      description: `Small meeting on ${date} at 19:00 with no lighting.`,
      overrides: { title: `Compact ${testInfo.project.name}`, start_date: date },
      idempotency_key: `compact-${testInfo.project.name}`,
    },
  });
  expect(response.status()).toBe(201);
  await page.goto("/events");
  await assertFloor(".calendar-event");
  await expectNoViewportOverflow(page);
  await activate(page.locator(".calendar-event").first());
  await expect(page.getByRole("dialog")).toBeVisible();
  await assertFloor(".status-tag");
  await page.keyboard.press("Escape");
  await page.goto("/settings");
  await page.locator('input[type="file"]').setInputFiles({
    name: "compact.csv", mimeType: "text/csv",
    buffer: Buffer.from("id,count,type\nCompact preview stand,2,stand\n"),
  });
  await expect(page.getByRole("dialog", { name: "Review inventory import" })).toBeVisible();
  await assertFloor(".csv-mapping-grid label, .csv-preview-table th, .csv-preview-table td");
  await assertControls();
  await page.screenshot({ path: testInfo.outputPath("compact-preview.png"), fullPage: true });
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
});

test("owner inventory removal and typed clear preserve private stock and retry safely", async ({ page }, testInfo) => {
  const suffix = testInfo.project.name;
  await signIn(page, `owner@phase-b5-${suffix}.test`);
  for (const [id, amount, scope] of [["chairs", 4, "shared"], ["table", 1, "shared"], ["private-case", 2, "personal"], ["chairs", 9, "personal"]] as const) {
    expect((await page.request.post("/api/inventory/items", { data: { id, amount, scope, type: "other" } })).status()).toBe(200);
  }
  await page.goto("/inventory");
  await activate(page.getByRole("button", { name: "Remove chairs", exact: true }));
  const remove = page.getByRole("dialog", { name: "Remove stock", exact: true });
  await expect(remove.getByLabel("Quantity")).toHaveAttribute("max", "4");
  await expect(remove).toContainText("Shared inventory");
  await remove.getByLabel("Quantity").fill("2");
  await remove.getByLabel("Reason").fill("Retired test stock");
  await activate(remove.getByRole("button", { name: "Remove from inventory", exact: true }));
  await expect(remove).toBeHidden();
  await expect(page.getByRole("button", { name: "Remove chairs", exact: true })).toBeFocused();
  let state = await (await page.request.get("/api/state")).json();
  expect(state.inventories.shared.items.find((item: { id: string }) => item.id === "chairs").count).toBe(2);
  await activate(page.getByRole("button", { name: "Remove table", exact: true }));
  await remove.getByLabel("Reason").fill("Retire last unit");
  await activate(remove.getByRole("button", { name: "Remove from inventory", exact: true }));
  await expect(remove).toBeHidden();
  await expect(page.getByRole("button", { name: "Remove table", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "All inventory", exact: true })).toBeFocused();
  await activate(page.getByRole("button", { name: "Archive empty-case", exact: true }));
  const archive = page.getByRole("dialog", { name: "Archive empty item", exact: true });
  await activate(archive.getByRole("button", { name: "Archive item", exact: true }));
  await expect(archive).toBeHidden();
  await expect(page.getByRole("button", { name: "Archive empty-case", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "All inventory", exact: true })).toBeFocused();
  const trigger = page.getByRole("button", { name: "Clear active inventory", exact: true });
  await activate(trigger);
  const dialog = page.getByRole("dialog", { name: "Clear active inventory?", exact: true });
  await expect(dialog.getByLabel("Type CLEAR INVENTORY")).toBeEnabled();
  await expect(dialog.locator("dl")).toContainText("Definitions to archive1");
  await expect(dialog.locator("dl")).toContainText("Available units to remove2");
  await dialog.getByLabel("Type CLEAR INVENTORY").fill("wrong");
  await expect(dialog.getByRole("button", { name: "Clear shared inventory", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
  await page.goto("/settings");
  await savePreference(page, () => activate(page.getByRole("button", { name: "dark", exact: true })));
  await savePreference(page, () => page.locator(".preference-row").filter({ hasText: "Text size" }).getByRole("combobox").selectOption("large"));
  await page.goto("/inventory");
  await activate(trigger);
  await dialog.getByLabel("Type CLEAR INVENTORY").fill("CLEAR INVENTORY");
  const submit = dialog.getByRole("button", { name: "Clear shared inventory", exact: true });
  await submit.focus();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Close dialog", exact: true })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(submit).toBeFocused();
  await expectNoViewportOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("clear-inventory-confirmation.png"), fullPage: true });
  const keys: string[] = [];
  await page.route("**/api/inventory/clear", async route => {
    keys.push(route.request().postDataJSON().idempotency_key);
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    if (keys.length === 1) await route.fulfill({ status: 200, contentType: "application/json", body: "unreadable-response" });
    else await route.fulfill({ response });
  });
  await activate(submit);
  await expect(dialog.getByRole("alert")).toBeVisible();
  await activate(submit);
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  state = await (await page.request.get("/api/state")).json();
  expect(state.inventories.shared.items).toHaveLength(0);
  expect(state.inventories.personal.items.find((item: { id: string }) => item.id === "private-case").count).toBe(2);
  expect(state.inventories.personal.items.find((item: { id: string }) => item.id === "chairs").count).toBe(9);
  await expectNoViewportOverflow(page);
  expect((await page.request.post("/api/auth/signout", { data: {} })).status()).toBe(200);
  await signIn(page, `admin@phase-b5-${suffix}.test`);
  await page.goto("/inventory");
  await expect(trigger).toHaveCount(0);
});

test("multilingual import wizard reconciles categories duplicates and cleanup", async ({ page }, testInfo) => {
  const errors = watchErrors(page);
  const suffix = testInfo.project.name;
  await signIn(page, `owner@phase-b-${suffix}.test`);
  const itemId = `review-ev-${suffix}`;
  const seeded = await page.request.post("/api/inventory/items", { data: {
    id: itemId, display_name: `EV ZLX-15P ${suffix}`, type: "pa", manufacturer: "Electro-Voice", model: "ZLX-15P", amount: 4, scope: "shared",
  } });
  expect(seeded.status()).toBe(200);
  const custom = `אירוח ${suffix}`;
  const csv = ["שם,כמות,סוג,יצרן,דגם,נוסף,הערות",
    `Electro Voice ZLX 15P ${suffix},2,רמקולים,Electro-Voice,ZLX-15P,ignored,במה`,
    `ميكروفون ${suffix},1,ميكروفونات,,,ignored,عرض موسيقي`,
    `Câble ${suffix},3,cable,,,ignored,Café`,
    `Custom ${suffix},1,${custom},,,ignored,خاص`,
    `Bad quantity ${suffix},wrong,cable,,,ignored,fix later`,
    `=Formula ${suffix},1,furniture,,,ignored,=1+1`,
    `EV ZLX-12P ${suffix},1,speaker,Electro-Voice,ZLX-12P,ignored,different model`,
  ].join("\n");
  await page.goto("/settings");
  const importButton = page.getByRole("button", { name: "Import to shared inventory", exact: true });
  await importButton.focus();
  await page.locator('input[type="file"]').setInputFiles({ name: "multilingual.csv", mimeType: "text/csv", buffer: Buffer.from(csv, "utf8") });
  const dialog = page.getByRole("dialog", { name: "Review inventory import" });
  await expect(dialog.getByLabel("Item name")).toHaveValue("שם");
  await expect(dialog.getByLabel("Quantity")).toHaveValue("כמות");
  await activate(dialog.getByRole("button", { name: "Continue", exact: true }));
  await dialog.getByRole("combobox", { name: `Map ${custom}`, exact: true }).selectOption("custom");
  await dialog.getByLabel("New category name").fill(custom);
  await dialog.locator(".import-review-row").filter({ has: page.getByRole("combobox", { name: `Map ${custom}`, exact: true }) }).getByRole("checkbox").check();
  await activate(dialog.getByRole("button", { name: "Continue", exact: true }));
  await dialog.getByRole("combobox", { name: "Decision for row 1", exact: true }).selectOption({ label: `Add quantity to EV ZLX-15P ${suffix}` });
  await dialog.getByRole("combobox", { name: "Decision for row 5", exact: true }).selectOption("skip");
  await expect(dialog.getByRole("combobox", { name: "Decision for row 7", exact: true })).toHaveValue("new");
  await activate(dialog.getByRole("button", { name: "Continue", exact: true }));
  await expect(dialog.getByRole("heading", { name: "Confirm import", exact: true })).toBeFocused();
  await expect(dialog.getByRole("button", { name: "Import inventory", exact: true })).toBeEnabled();
  await expectNoViewportOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("import-final-preview.png"), fullPage: true });
  await activate(dialog.getByRole("button", { name: "Back", exact: true }));
  await expect(dialog.getByRole("combobox", { name: "Decision for row 1", exact: true })).not.toHaveValue("new");
  await activate(dialog.getByRole("button", { name: "Continue", exact: true }));
  const importKeys: string[] = [];
  await page.route("**/api/inventory/import.commit", async route => {
    importKeys.push(route.request().postDataJSON().idempotency_key);
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    // The first command commits, but its response is unreadable by the browser.
    if (importKeys.length === 1) await route.fulfill({ status: 200, contentType: "application/json", body: "unreadable-response" });
    else await route.fulfill({ response });
  });
  await activate(dialog.getByRole("button", { name: "Import inventory", exact: true }));
  await expect(dialog.getByRole("alert")).toBeVisible();
  await activate(dialog.getByRole("button", { name: "Import inventory", exact: true }));
  await expect(dialog).toBeHidden();
  expect(importKeys).toHaveLength(2);
  expect(importKeys[1]).toBe(importKeys[0]);
  await expect(importButton).toBeFocused();
  const state = await (await page.request.get("/api/state")).json();
  expect(state.inventory.items.find((item: { id: string }) => item.id === itemId).count).toBe(6);
  const exported = await (await page.request.get("/api/inventory/export.csv")).text();
  expect(exported).toContain(`ميكروفون ${suffix}`);
  expect(exported).toContain(`Câble ${suffix}`);
  expect(exported).toContain("'=1+1");
  await page.goto("/inventory/cleanup");
  await expect(page.getByRole("heading", { name: "Product details" })).toBeVisible();
  await expectNoViewportOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("inventory-cleanup.png"), fullPage: true });
  await page.getByLabel("Search product details").fill(`Câble ${suffix}`);
  await activate(page.getByRole("link", { name: `Edit Câble ${suffix}` }));
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByLabel("Item name", { exact: true })).toHaveValue(`Câble ${suffix}`);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await expectNoViewportOverflow(page);
  expect(errors).toEqual([]);
});

test("CSV mapping and appearance controls are keyboard reachable", async ({ page }, testInfo) => {
  const errors = watchErrors(page);
  await signIn(page);
  await page.goto("/settings");
  await page.locator('input[type="file"]').setInputFiles({
    name: "invalid-encoding.csv", mimeType: "text/csv",
    buffer: Buffer.from([0x63, 0x61, 0x66, 0xe9]),
  });
  await expect(page.getByText("CSV must be UTF-8 encoded. Export as CSV UTF-8 and try again.")).toBeVisible();
  await expect(page.getByRole("dialog")).toBeHidden();
  await savePreference(page, () => activate(page.getByRole("button", { name: "dark" })));
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await savePreference(page, () => page.locator(".preference-row").filter({ hasText: "Text size" }).getByRole("combobox").selectOption("largest"));
  await expect(page.locator("html")).toHaveAttribute("data-font-scale", "largest");
  await savePreference(page, () => activate(page.getByRole("button", { name: "Compact", exact: true })));
  await expect(page.locator("html")).toHaveAttribute("data-density", "compact");
  await page.locator('input[type="file"]').setInputFiles({
    name: "inventory.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(`Equipment,Units,Kind\nQA CSV ${testInfo.project.name} stand,2,stand\n`),
  });
  const dialog = page.getByRole("dialog", { name: "Review inventory import" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Item name").selectOption("Equipment");
  await dialog.getByLabel("Quantity").selectOption("Units");
  await dialog.getByLabel("Category").selectOption("Kind");
  await expect(dialog.getByText(`QA CSV ${testInfo.project.name} stand`, { exact: true })).toBeVisible();
  await expectNoViewportOverflow(page);
  await activate(dialog.getByRole("button", { name: "Continue", exact: true }));
  await expect(dialog.getByRole("heading", { name: "Review categories", exact: true })).toBeVisible();
  await activate(dialog.getByRole("button", { name: "Continue", exact: true }));
  await expect(dialog.getByRole("heading", { name: "Review duplicates", exact: true })).toBeVisible();
  await activate(dialog.getByRole("button", { name: "Continue", exact: true }));
  await activate(dialog.getByRole("button", { name: "Import inventory", exact: true }));
  await expect(dialog).toBeHidden();
  await page.goto("/inventory");
  await page.getByPlaceholder("Search inventory").fill(`QA CSV ${testInfo.project.name} stand`);
  await expect(page.locator(".inventory-table tbody tr")).toHaveCount(1);
  await expectNoViewportOverflow(page);
  expect(errors).toEqual([]);
});
