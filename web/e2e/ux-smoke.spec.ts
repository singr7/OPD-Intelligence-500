// A viewport smoke check for the S-UX.6 surfaces, not a behavioural test: it
// proves the same markup lands correctly on the portrait kiosk and on a laptop —
// no horizontal scroll, and the primary action reachable without hunting.
import { expect, test } from "@playwright/test";

const SIZES = [
  { width: 1080, height: 1920, tag: "portrait-kiosk" },
  { width: 1366, height: 768, tag: "laptop" },
];

for (const size of SIZES) {
  test(`registration fits the ${size.tag}`, async ({ page }) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    await page.goto("/kiosk");
    await page.getByTestId("welcome-lang-hi").click();
    await page.getByTestId("caregiver-self").click();
    await page.getByTestId("patient-name").fill("सुनीता शर्मा");
    await page.getByTestId("patient-age").fill("34");
    await page.getByTestId("patient-sex-female").click();
    await page.waitForTimeout(400);
    await page.screenshot({ path: `screenshots/s6/ux-${size.tag}-details.png` });

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(overflow, "no horizontal scroll").toBe(false);
    const box = (await page.getByTestId("details-next").boundingBox())!;
    expect(box.y + box.height).toBeLessThanOrEqual(size.height);
  });
}
