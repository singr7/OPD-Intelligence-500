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

async function submitAnswer(page: Page) {
  const button = page.getByTestId("answer-submit");
  await expect(button).toBeEnabled({ timeout: 15_000 });
  await button.click();
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
    await submitAnswer(page);
  } else if (type === "scale") {
    await page.getByTestId("face").nth(3).click();
    await submitAnswer(page);
  } else if (type === "number") {
    await submitAnswer(page);
  } else if (type === "free_voice") {
    await typeInto(page, "mujhe pet mein dard hai");
    await submitAnswer(page);
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

  // 3. Arrival identity — the returning-patient branch (AR3). A phone and a
  //    hospital ID, both optional, neither of them a gate.
  await page.getByTestId("caregiver-self").click();
  await expect(page.locator("main")).toHaveAttribute("data-screen", "returning");
  await shot(page, "02a-returning");

  await page.getByTestId("returning-yes").click();
  await expect(page.locator("main")).toHaveAttribute("data-screen", "arrivalPhone");
  for (const digit of "9876500011") await page.getByTestId(`arrival-phone-${digit}`).click();
  await expect(page.getByTestId("arrival-phone-display")).toHaveText("9876500011");
  await shot(page, "02b-arrival-phone");

  await page.getByTestId("arrival-phone-next").click();
  await expect(page.locator("main")).toHaveAttribute("data-screen", "arrivalId");
  await page.getByTestId("arrival-external-id").fill("UHC-2291");
  await shot(page, "02c-arrival-id");
  await page.getByTestId("arrival-id-next").click();

  // 4. Registration details — name, age, gender, phone (S-UX.6). The phone
  //    arrives pre-filled from the keypad, and the acknowledgement is here.
  await expect(page.locator("main")).toHaveAttribute("data-screen", "details");
  await expect(page.getByTestId("arrival-ack")).toBeVisible();
  await expect(page.getByTestId("patient-phone")).toHaveValue("9876500011");
  await page.getByTestId("patient-name").fill("सीमा देवी");
  await page.getByTestId("patient-age").fill("54");
  await page.getByTestId("patient-sex-female").click();
  await expect(page.getByTestId("summary-patient")).toHaveText("सीमा देवी");
  await shot(page, "02d-details");
  await page.getByTestId("details-next").click();

  // 4. Chief complaint (tap-to-type fallback in headless).
  await expect(page.locator("main")).toHaveAttribute("data-screen", "complaint");
  await typeInto(page, "mujhe seene mein dard aur khaansi hai");
  await shot(page, "03-complaint");

  // 5. Department chooser (fake classifier is uncertain → honour needs_human).
  await page.getByTestId("cc-next").click();
  await expect(page.locator("main")).toHaveAttribute("data-screen", "chooser", {
    timeout: 20_000,
  });
  await shot(page, "04-chooser");

  // 6. First tree question.
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

  // 6b. The allergy question (SESSION-ALLERGY) — asked after the tree runs out,
  //     of every patient in every department, on every tier. Three answers, and
  //     "I don't know" is one of them.
  await expect(page.locator("main")).toHaveAttribute("data-screen", "allergy", {
    timeout: 15_000,
  });
  await expect(page.getByTestId("allergy-unsure")).toBeVisible();
  await shot(page, "06a-allergy-ask");

  // Spoken, like every other thing this kiosk asks in words — headless has no
  // Web Speech, so `typeInto` takes the "type instead" path the mic offers.
  await page.getByTestId("allergy-yes").click();
  await typeInto(page, "पेनिसिलिन");
  await shot(page, "06b-allergy-which");
  await page.getByTestId("allergy-submit").click();

  // 7. Read-back + confirm.
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

test("tablet matrix keeps name, summary and primary action inside the viewport", async ({
  page,
}) => {
  const viewports = [
    { width: 1280, height: 800 },
    { width: 1024, height: 768 },
    { width: 800, height: 1280 },
  ];
  for (const viewport of viewports) {
    for (const scale of [1, 2]) {
      await page.setViewportSize(viewport);
      await page.goto("/kiosk");
      if (scale === 2) await page.addStyleTag({ content: "html { font-size: 200%; }" });
      await page.getByTestId("welcome-lang-te").click();
      await page.getByTestId("caregiver-self").click();
      // First-time patients skip the arrival pair entirely.
      await page.getByTestId("returning-no").click();
      await page.getByTestId("patient-name").fill("శ్రీమతి వెంకట లక్ష్మీ దేవి");

      const metrics = await page.evaluate(() => {
        const action = document.querySelector<HTMLElement>('[data-testid="details-next"]');
        const box = action?.getBoundingClientRect();
        return {
          horizontalOverflow:
            document.documentElement.scrollWidth > document.documentElement.clientWidth,
          actionVisible:
            !!box &&
            box.width > 0 &&
            box.height >= 64 &&
            box.left >= 0 &&
            box.right <= window.innerWidth &&
            box.top >= 0 &&
            box.bottom <= window.innerHeight,
        };
      });
      expect(metrics.horizontalOverflow, `${viewport.width}x${viewport.height} @ ${scale}x`).toBe(
        false
      );
      expect(metrics.actionVisible, `${viewport.width}x${viewport.height} @ ${scale}x`).toBe(true);
      await expect(page.getByTestId("summary-patient")).toHaveText(
        "శ్రీమతి వెంకట లక్ష్మీ దేవి"
      );
    }
  }
});

test("english welcome renders", async ({ page }) => {
  await page.goto("/kiosk");
  await page.getByTestId("welcome-lang-en").click();
  await expect(page.getByText("For myself")).toBeVisible();
  await shot(page, "09-caregiver-en");
});
