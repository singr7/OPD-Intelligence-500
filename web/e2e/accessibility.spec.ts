import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const publicRoutes = [
  { path: "/", name: "role gateway" },
  { path: "/doctor", name: "staff sign-in" },
  { path: "/kiosk", name: "kiosk welcome" },
];

for (const route of publicRoutes) {
  test(`${route.name} has no automatically detectable accessibility violations`, async ({
    page,
  }) => {
    await page.goto(route.path);
    await expect(page.locator("body")).toBeVisible();
    await page.waitForLoadState("networkidle");

    const results = await new AxeBuilder({ page }).analyze();

    expect(results.violations).toEqual([]);
  });
}
