import { test, expect, Page } from "@playwright/test";
import path from "node:path";

// Full kiosk intake against the local stack (S6 AC), capturing a screenshot of
// every patient-facing screen for the doc 04 §5 self-critique. Deterministic:
// headless has no Web Speech, so Q1 and free_voice use tap-to-type, and the fake
// classifier is uncertain — which exercises the department chooser on the way in.

const SHOTS = path.join(__dirname, "..", "screenshots", "s6");

async function shot(page: Page, name: string) {
  await page.waitForTimeout(350); // let the entrance settle
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`) });
}

async function typeInto(page: Page, text: string) {
  // In Chromium webkitSpeechRecognition exists, so the mic is offered and the
  // textarea hides behind a "type instead" toggle — click it if present.
  const toggle = page.getByTestId("type-toggle");
  if (await toggle.count()) await toggle.click();
  await page.getByRole("textbox").fill(text);
}

async function answerCurrent(page: Page): Promise<boolean> {
  // Returns false once we've left the question flow (readback reached).
  const screen = await page.getAttribute("main", "data-screen");
  if (screen !== "question") return false;
  const type = await page.getAttribute("main", "data-node-type");

  if (type === "single") {
    await page.getByTestId("option").first().click();
  } else if (type === "multi" || type === "body_map") {
    await page.getByTestId("option").first().click();
    await page.getByTestId("answer-submit").click();
  } else if (type === "scale") {
    await page.getByTestId("face").nth(3).click();
    await page.getByTestId("answer-submit").click();
  } else if (type === "number") {
    await page.getByTestId("answer-submit").click();
  } else if (type === "free_voice") {
    await typeInto(page, "mujhe pet mein dard hai");
    await page.getByTestId("answer-submit").click();
  }
  await page.waitForTimeout(400);
  return true;
}

test("full hindi kiosk intake, welcome → token", async ({ page }) => {
  await page.goto("/kiosk");

  // 1. Welcome / language.
  await expect(page.locator("main")).toHaveAttribute("data-screen", "welcome");
  await shot(page, "01-welcome");

  // 2. Caregiver.
  await page.getByTestId("welcome-lang-hi").click();
  await expect(page.locator("main")).toHaveAttribute("data-screen", "caregiver");
  await shot(page, "02-caregiver");

  // 3. Chief complaint (tap-to-type fallback in headless).
  await page.getByText("मैं अपने लिए").click();
  await expect(page.locator("main")).toHaveAttribute("data-screen", "complaint");
  await typeInto(page, "mujhe seene mein dard aur khaansi hai");
  await shot(page, "03-complaint");

  // 4. Department chooser (fake classifier is uncertain → honour needs_human).
  await page.getByTestId("cc-next").click();
  await expect(page.locator("main")).toHaveAttribute("data-screen", "chooser", {
    timeout: 20_000,
  });
  await shot(page, "04-chooser");

  // 5. First tree question.
  await page.getByTestId("option").filter({ hasText: "Medical Oncology" }).click();
  await expect(page.locator("main")).toHaveAttribute("data-screen", "question", {
    timeout: 20_000,
  });
  await shot(page, "05-question-single");

  // Walk the rest of the tree, screenshotting the first of each distinct type.
  const seenTypes = new Set<string>(["single"]);
  for (let i = 0; i < 40; i++) {
    const screen = await page.getAttribute("main", "data-screen");
    if (screen !== "question") break;
    const type = (await page.getAttribute("main", "data-node-type")) ?? "";
    if (type && !seenTypes.has(type)) {
      seenTypes.add(type);
      await shot(page, `06-question-${type}`);
    }
    const stillGoing = await answerCurrent(page);
    if (!stillGoing) break;
  }

  // 6. Read-back + confirm.
  await expect(page.locator("main")).toHaveAttribute("data-screen", "readback", {
    timeout: 15_000,
  });
  await shot(page, "07-readback");

  // 7. Token.
  await page.getByTestId("confirm").click();
  await expect(page.locator("main")).toHaveAttribute("data-screen", "token", {
    timeout: 15_000,
  });
  await expect(page.locator("main")).toContainText("टोकन");
  await shot(page, "08-token");
});

test("english welcome renders", async ({ page }) => {
  await page.goto("/kiosk");
  await page.getByTestId("welcome-lang-en").click();
  await expect(page.getByText("For myself")).toBeVisible();
  await shot(page, "09-caregiver-en");
});
