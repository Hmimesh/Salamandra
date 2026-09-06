import { expect, test as base, type Page, type Locator } from "@playwright/test";

const test = base.extend<{ errorGuard: void }>({
  errorGuard: [async ({ page }, use) => {
    const errors = watchErrors(page);
    await use();
    expect(errors).toEqual([]);
  }, { auto: true }],
});

async function activate(control: Locator) {
  await control.focus();
  await expect(control).toBeFocused();
  await control.press("Enter");
}


async function signIn(page: Page, email = "owner@playwright.test") {
  await page.goto("/login");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill("playwright-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("main").getByRole("heading").first()).toBeVisible();
  await page.goto("/settings");
  await activate(page.getByRole("button", { name: "light", exact: true }));
  await page.locator(".preference-row").filter({ hasText: "Text size" }).getByRole("combobox").selectOption("comfortable");
  await activate(page.getByRole("button", { name: "Comfortable", exact: true }));
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
  await activate(catalog.getByRole("button", { name: "Add XLR Cable 10m", exact: true }));
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
  await activate(catalog.getByRole("button", { name: "Add XLR Cable 10m", exact: true }));
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
      await activate(page.getByRole("button", { name: theme, exact: true }));
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await page.locator(".preference-row").filter({ hasText: "Text size" }).getByRole("combobox").selectOption(scale);
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

test("CSV mapping and appearance controls are keyboard reachable", async ({ page }, testInfo) => {
  const errors = watchErrors(page);
  await signIn(page);
  await page.goto("/settings");
  await activate(page.getByRole("button", { name: "dark" }));
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.locator(".preference-row").filter({ hasText: "Text size" }).getByRole("combobox").selectOption("largest");
  await expect(page.locator("html")).toHaveAttribute("data-font-scale", "largest");
  await activate(page.getByRole("button", { name: "Compact", exact: true }));
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
  await activate(dialog.getByRole("button", { name: "Import inventory", exact: true }));
  await expect(dialog).toBeHidden();
  await page.goto("/inventory");
  await page.getByPlaceholder("Search inventory").fill(`QA CSV ${testInfo.project.name} stand`);
  await expect(page.locator(".inventory-table tbody tr")).toHaveCount(1);
  await expectNoViewportOverflow(page);
  expect(errors).toEqual([]);
});
