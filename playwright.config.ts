import { defineConfig } from "@playwright/test";


const baseURL = "http://127.0.0.1:4173";

export default defineConfig({
  testDir: "tests/e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "line",
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  globalSetup: "./tests/e2e_setup.ts",
  projects: [
    { name: "mobile-360", use: { viewport: { width: 360, height: 800 } } },
    { name: "tablet-768", use: { viewport: { width: 768, height: 1024 } } },
    { name: "desktop-1280", use: { viewport: { width: 1280, height: 900 } } },
    { name: "wide-1440", use: { viewport: { width: 1440, height: 1000 } } },
    // A 1280x900 browser at 200% zoom has 640x450 CSS pixels and doubled DPR.
    { name: "desktop-200-percent", use: { viewport: { width: 640, height: 450 }, deviceScaleFactor: 2 } },
  ],
});
