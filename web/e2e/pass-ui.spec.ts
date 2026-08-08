// The intake boarding pass on a real screen (doc 23 §7). **This project is the
// session AC**: a patient finishes an intake and can see the piece of paper
// they are about to be handed, the button prints it and then says re-print, and
// the bytes that reach the bridge are a real ESC/POS raster job built by a real
// browser from real shaped text.
//
// The unit suites (`pass.spec.ts`, `pass-raster.spec.ts`) prove fitment and
// byte structure with a fake measurer. This one proves the parts they cannot:
// that the layout survives contact with an actual font stack, that the preview
// is where a person can see it, and that the printed page is 80 x 200mm.
//
// Live stack + a seeded database, so it is run explicitly (`npm run e2e:pass`)
// with the dev server started with `NEXT_PUBLIC_PRINT_BRIDGE_URL` set — see
// STATE.md. It creates a real visit and a real token on whatever database it
// points at. Dev boxes only.

import { test, expect, Page } from "@playwright/test";
import path from "node:path";

const SHOTS = path.join(__dirname, "..", "screenshots", "pass");
const BRIDGE = "http://127.0.0.1:9110/print";

async function shot(page: Page, name: string) {
  await page.waitForTimeout(350);
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`) });
}

async function typeInto(page: Page, text: string) {
  const toggle = page.getByTestId("type-toggle");
  if (await toggle.count()) await toggle.click();
  await page.getByRole("textbox").fill(text);
}

/** Welcome → token, in Hindi, with every field the pass prints actually given:
 *  a name, an age, a sex, a mobile and a UHC ID. The pass is the only surface
 *  that puts all five on one object, so an intake that skips them proves
 *  nothing about it. */
async function intakeToToken(page: Page) {
  await page.goto("/kiosk");
  await page.getByTestId("welcome-lang-hi").click();
  await page.getByTestId("caregiver-self").click();
  await page.getByTestId("returning-yes").click();
  for (const digit of "9876500011") await page.getByTestId(`arrival-phone-${digit}`).click();
  await page.getByTestId("arrival-phone-next").click();
  await page.getByTestId("arrival-external-id").fill("UHC-2291");
  await page.getByTestId("arrival-id-next").click();

  await page.getByTestId("patient-name").fill("सीमा देवी");
  await page.getByTestId("patient-age").fill("54");
  await page.getByTestId("patient-sex-female").click();
  await page.getByTestId("details-next").click();

  await expect(page.locator("main")).toHaveAttribute("data-screen", "complaint");
  await typeInto(page, "mujhe seene mein dard aur khaansi hai");
  await page.getByTestId("cc-next").click();

  await expect(page.locator("main")).toHaveAttribute("data-screen", "chooser", {
    timeout: 20_000,
  });
  await page.getByTestId("option").filter({ hasText: "Medical Oncology" }).click();
  await expect(page.locator("main")).toHaveAttribute("data-screen", "question", {
    timeout: 20_000,
  });

  for (let i = 0; i < 40; i++) {
    if ((await page.getAttribute("main", "data-screen")) !== "question") break;
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
      await typeInto(page, "do mahine se");
      await page.getByTestId("answer-submit").click();
    }
    await page.waitForTimeout(350);
  }

  await expect(page.locator("main")).toHaveAttribute("data-screen", "readback", {
    timeout: 15_000,
  });
  await page.getByTestId("confirm").click();
  await expect(page.locator("main")).toHaveAttribute("data-screen", "token", {
    timeout: 15_000,
  });
  await expect(page.getByTestId("pass-preview")).toBeAttached({ timeout: 15_000 });
}

test("the patient sees the paper before they are handed it", async ({ page }) => {
  await intakeToToken(page);
  await shot(page, "01-token-with-pass");

  const preview = page.getByTestId("pass-preview");

  // `toBeVisible` is not a visibility assertion — an element behind another one
  // passes it. Assert geometry: the pass is on screen, it is a pass-shaped
  // object, and nothing is sitting on top of it.
  const box = await preview.boundingBox();
  expect(box).not.toBeNull();
  const { x, y, width, height } = box!;
  const viewport = page.viewportSize()!;
  expect(x).toBeGreaterThanOrEqual(0);
  expect(y).toBeGreaterThanOrEqual(0);
  expect(x + width).toBeLessThanOrEqual(viewport.width);
  expect(y + height).toBeLessThanOrEqual(viewport.height);
  // 80mm x 200mm is an aspect ratio of 0.4, and it is preserved or the pass is
  // lying about what will come out of the printer.
  expect(width / height).toBeCloseTo(0.4, 2);

  // The thing the patient's finger would land on is the pass, not something
  // drawn over it.
  const onTop = await page.evaluate(
    ([cx, cy]) => document.elementFromPoint(cx, cy)?.closest("svg")?.getAttribute("data-testid"),
    [x + width / 2, y + height / 2]
  );
  expect(onTop).toBe("pass-preview");
});

test("the pass carries the six things the pilot asked for", async ({ page }) => {
  await intakeToToken(page);
  const preview = page.getByTestId("pass-preview");
  const text = (await preview.innerText().catch(() => "")) || (await preview.textContent()) || "";

  const tokenNo = (await page.getByTestId("token-number").textContent())?.trim();
  expect(tokenNo).toBeTruthy();
  expect(text).toContain(tokenNo!); // 1. the token number
  expect(text).toContain("सीमा देवी"); // 2. the name, in its own script
  expect(text).toContain("9876500011"); // 3. the mobile, unmasked (§8)
  expect(text).toContain("54"); // 4. age
  expect(text).toContain("UHC-2291"); // 5. the UHC ID
  expect(text).toContain("INTAKE SUMMARY"); // 6. and the summary, at max real estate
  expect(text).toContain("mujhe seene mein dard aur khaansi hai");

  // Bilingual where the patient reads (§4): the heading in Hindi and English.
  expect(text).toContain("आपकी जानकारी");
  // And the stub the desk tears off repeats the token.
  expect(text.split(tokenNo!).length - 1).toBeGreaterThanOrEqual(2);
});

test("Print sends a real ESC/POS raster to the bridge, and then says Re-print", async ({
  page,
}) => {
  const posted: Buffer[] = [];
  await page.route(BRIDGE, async (route) => {
    const body = route.request().postDataBuffer();
    if (body) posted.push(body);
    await route.fulfill({ status: 200, body: "ok" });
  });

  await intakeToToken(page);
  const button = page.getByTestId("token-print");
  await expect(button).toContainText("पास छापें");

  // The bytes are rendered at token time, so give the raster a moment to land
  // before pressing — this is the pre-render of §6, and pressing early is
  // exactly what it is designed to survive.
  await page.waitForTimeout(1_500);
  await button.click();

  await expect(button).toContainText("फिर से छापें", { timeout: 10_000 });
  await shot(page, "02-after-print");

  expect(posted.length).toBe(1);
  const bytes = posted[0];
  // A real browser rasterised real shaped Devanagari into this.
  expect([...bytes.subarray(0, 2)]).toEqual([0x1b, 0x40]); // ESC @
  expect([...bytes.subarray(2, 5)]).toEqual([0x1d, 0x76, 0x30]); // GS v 0
  // ESC @ (2) + GS v 0 (3) then m, xL, xH, yL, yH — the density byte comes
  // first, so the row length starts at index 6.
  expect(bytes[5]).toBe(0x00); // m — normal density
  expect(bytes[7] * 256 + bytes[6]).toBe(72); // 72 bytes a row
  expect(bytes[9] * 256 + bytes[8]).toBe(1600); // 1600 rows
  expect([...bytes.subarray(-3)]).toEqual([0x1d, 0x56, 0x01]); // partial cut
  expect(bytes.length).toBe(2 + 8 + 72 * 1600 + 3 + 3);

  // Ink, and not too much of it: a blank raster means the SVG never rendered,
  // and a solid one means the background or the threshold is inverted.
  const ink = bytes.subarray(10, 10 + 72 * 1600).reduce((n, b) => n + popcount(b), 0);
  expect(ink).toBeGreaterThan(2_000);
  expect(ink).toBeLessThan(576 * 1600 * 0.35);

  // Re-print re-sends the held bytes — identical paper, not a second render.
  await button.click();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1].equals(posted[0])).toBeTruthy();
});

test("the browser path prints one 80 x 200mm page, not a scaled A4", async ({ page }) => {
  await intakeToToken(page);
  // `preferCSSPageSize` honours the `@page` rule, which is the thing under
  // test: an office printer with no thermal bridge must still lay the pass down
  // at its true physical size (§3).
  const pdf = await page.pdf({ preferCSSPageSize: true });
  const raw = pdf.toString("latin1");
  const media = raw.match(/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)/);
  expect(media, "no MediaBox in the printed PDF").not.toBeNull();
  // 80mm = 226.77pt, 200mm = 566.93pt.
  expect(Number(media![1])).toBeCloseTo(226.77, 0);
  expect(Number(media![2])).toBeCloseTo(566.93, 0);
});

test("the token screen survives the tablet matrix with the pass on it", async ({ page }) => {
  // The pass added a second pane to a screen whose one job is a numeral
  // readable from three metres. S-UX.6's matrix exists because a kiosk is
  // whatever tablet the hospital bought, and this screen had never been
  // measured on it — so measure it, rather than assume the flex wrap holds.
  await intakeToToken(page);

  for (const viewport of [
    { width: 1280, height: 800 },
    { width: 1024, height: 768 },
    { width: 800, height: 1280 },
  ]) {
    await page.setViewportSize(viewport);
    await page.waitForTimeout(250);
    const label = `${viewport.width}x${viewport.height}`;

    const metrics = await page.evaluate(() => {
      // Rewind whatever is scrolling, so "is anything clipped" is asked at rest
      // rather than wherever a previous viewport left it.
      for (
        let node = document.querySelector('[data-testid="token-number"]')?.parentElement;
        node;
        node = node.parentElement
      ) {
        if (node.scrollHeight > node.clientHeight) node.scrollTop = 0;
      }
      const rect = (selector: string) =>
        document.querySelector(selector)?.getBoundingClientRect() ?? null;
      const numeral = rect('[data-testid="token-number"]');
      const pass = rect('[data-testid="pass-preview"]');
      const button = rect('[data-testid="token-print"]');
      const overlaps = (a: DOMRect | null, b: DOMRect | null) =>
        !!a && !!b && a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
      return {
        horizontalOverflow:
          document.documentElement.scrollWidth > document.documentElement.clientWidth,
        numeralWidth: numeral?.width ?? 0,
        // Nothing may sit above the top edge at rest. A centred flex box that
        // overflows pushes its first child up there, and scrolling cannot
        // bring it back — the pass pane made this screen tall enough to do
        // exactly that on the portrait tablet.
        clippedAtTop: Math.min(numeral?.top ?? 0, rect('[data-testid="token-name"]')?.top ?? 0) < 0,
        passWidth: pass?.width ?? 0,
        passInsideWidth: !!pass && pass.left >= 0 && pass.right <= window.innerWidth,
        buttonTall: !!button && button.height >= 64,
        buttonInsideWidth: !!button && button.left >= 0 && button.right <= window.innerWidth,
        // The two panes must never sit on top of each other. On portrait they
        // stack and the screen scrolls, which is the existing decision for this
        // screen: a scrollbar beats two overlapping controls on a live kiosk.
        collide: overlaps(numeral, pass) || overlaps(numeral, button),
      };
    });

    expect(metrics.horizontalOverflow, label).toBe(false);
    expect(metrics.numeralWidth, label).toBeGreaterThan(0);
    expect(metrics.clippedAtTop, label).toBe(false);
    expect(metrics.passWidth, label).toBeGreaterThan(0);
    expect(metrics.passInsideWidth, label).toBe(true);
    expect(metrics.buttonTall, label).toBe(true);
    expect(metrics.buttonInsideWidth, label).toBe(true);
    expect(metrics.collide, label).toBe(false);

    await shot(page, `03-token-${label}`);
  }
});

function popcount(byte: number): number {
  let n = byte;
  let count = 0;
  while (n) {
    count += n & 1;
    n >>= 1;
  }
  return count;
}
