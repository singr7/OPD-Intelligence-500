// The pass's own words, in all four pilot languages (doc 23 §4).
//
// These are kept apart from the kiosk's `T` table for the same reason the old
// slip's two phrases were: nothing here is ever on screen as UI chrome — it is
// ink on a piece of paper, set in small caps beside a value, and the register is
// a boarding pass rather than a conversation. `T` is warm and second-person
// (doc 04 law 7); `TOKEN` / `MOBILE` / `ISSUED` are labels on a document.
//
// Every label is bilingual: the patient's language first, English second, so the
// desk can read a pass whose owner chose Telugu (§4). The English half is a
// constant, not a fifth translation.
//
// mr/te are model-drafted like the rest of S13's text and join the same native
// clinical review gate.

import type { KioskLang } from "../i18n";

type Str = Record<KioskLang, string>;

/** Labels that sit beside a value in the identity grid and on the stub. */
export const PASS_LABELS = {
  token: {
    hi: "टोकन",
    en: "TOKEN",
    mr: "टोकन",
    te: "టోకెన్",
  } as Str,
  ageSex: {
    hi: "उम्र / लिंग",
    en: "AGE / SEX",
    mr: "वय / लिंग",
    te: "వయస్సు / లింగం",
  } as Str,
  mobile: {
    hi: "मोबाइल",
    en: "MOBILE",
    mr: "मोबाइल",
    te: "మొబైల్",
  } as Str,
  uhcId: {
    hi: "यूएचसी आईडी",
    en: "UHC ID",
    mr: "यूएचसी आयडी",
    te: "యూహెచ్‌సీ ఐడీ",
  } as Str,
  issued: {
    hi: "जारी",
    en: "ISSUED",
    mr: "जारी",
    te: "జారీ",
  } as Str,
  summary: {
    hi: "आपकी जानकारी",
    en: "INTAKE SUMMARY",
    mr: "तुमची माहिती",
    te: "మీ సమాచారం",
  } as Str,
  complaint: {
    hi: "मुख्य शिकायत",
    en: "CHIEF COMPLAINT",
    mr: "मुख्य तक्रार",
    te: "ప్రధాన సమస్య",
  } as Str,
} as const;

/** The reversed urgent band (§4). It says *go to the desk* and never why —
 *  same rule as the public board: red-flag reasons are not printed (§8). */
export const PASS_URGENT: Str = {
  hi: "** तुरंत डेस्क पर दिखाएँ **",
  en: "** SHOW AT DESK NOW **",
  mr: "** ताबडतोब डेस्कवर दाखवा **",
  te: "** వెంటనే డెస్క్ వద్ద చూపించండి **",
};

/** The reserved last line of the summary band (§5.3). The pass never silently
 *  drops an answer; it says that it abbreviated and where the rest is.
 *  `{n}` is the number of answers that did not fit. */
export const PASS_MORE: Str = {
  hi: "+ {n} और जवाब — पूरा रिकॉर्ड डॉक्टर के पास है",
  en: "+ {n} more answers — full record is with the doctor",
  mr: "+ {n} आणखी उत्तरे — संपूर्ण नोंद डॉक्टरांकडे आहे",
  te: "+ {n} మరిన్ని సమాధానాలు — పూర్తి రికార్డు డాక్టర్ వద్ద ఉంది",
};

/** The lozenge in the header. Deliberately untranslated: it is the name of the
 *  document, printed the way a boarding pass prints its airline code. */
export const PASS_LOZENGE = "OPD PASS";

/** Printed wherever a field is absent. A blank looks like a rendering fault; an
 *  em dash is the pass stating that it was not given the value (§4). */
export const PASS_ABSENT = "—";

export type PassLabelKey = keyof typeof PASS_LABELS;

/** The patient's language then English, for a label that owns two lines. */
export function bilingual(key: PassLabelKey, lang: KioskLang): string[] {
  const own = PASS_LABELS[key][lang];
  const english = PASS_LABELS[key].en;
  return own === english ? [english] : [own, english];
}

/** The same pair on one line, for a label that owns the full width. */
export function bilingualInline(key: PassLabelKey, lang: KioskLang): string {
  const [own, english] = bilingual(key, lang);
  return english === undefined ? own : `${own} · ${english}`;
}

/**
 * The identity grid's four field labels are **English only**, and that is a
 * decision rather than an omission.
 *
 * §4 asks for bilingual labels so the desk can read a pass whose owner chose
 * Telugu — the English half *is* the desk's half. In the grid there is no room
 * for both: `यूएचसी आईडी · UHC ID` measures ~26mm against a 14mm label column,
 * so a bilingual label there would be fitted down to about 1.8mm, which prints
 * as a smudge on a 203dpi head and serves neither reader. Field labels on a
 * real boarding pass are in one language for the same reason.
 *
 * So the split is by audience, not by band: everything the *patient* reads —
 * the token label, the summary heading, the chief-complaint label and the
 * urgent band — is bilingual at a size that survives a thermal printer, and the
 * four administrative fields beside their own self-describing values are not.
 */
export function fieldLabel(key: PassLabelKey): string {
  return PASS_LABELS[key].en;
}
