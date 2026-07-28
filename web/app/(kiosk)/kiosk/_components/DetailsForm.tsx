"use client";

// The registration screen (S-UX.6): name, age, gender, phone — asked once, in
// one place, before the clinical walk starts.
//
// Deliberately typed, not spoken. The kiosk speaks everywhere else because most
// patients here read slowly or not at all, but these four facts are the ones that
// end up printed on a prescription and matched against a queue: a misheard name
// is a different patient, and a misheard digit is a phone number that reaches
// nobody. So the mic stays off this screen and the attendant or the patient taps
// it in, with the field labels still read aloud by the stage above.
//
// Only the name is required. An age or a number the patient does not want to give
// must never block a token — the fields say which are optional rather than
// letting someone discover it by being refused.

import { KioskLang, t } from "../_lib/i18n";
import type { PatientDetails } from "../_lib/api";
import { Icon } from "../_lib/icons";
import s from "../kiosk.module.css";

export const emptyDetails: PatientDetails = {
  name: "",
  age: null,
  sex: null,
  phone: "",
};

/** Enough to start: a name. Everything else is the patient's to withhold. */
export function detailsComplete(details: PatientDetails): boolean {
  const name = details.name.trim();
  return name.length > 0 && name.length <= 200;
}

const SEX_CHOICES: {
  id: NonNullable<PatientDetails["sex"]>;
  label: "sexMale" | "sexFemale" | "sexOther";
}[] = [
  { id: "male", label: "sexMale" },
  { id: "female", label: "sexFemale" },
  { id: "other", label: "sexOther" },
];

export function DetailsForm({
  lang,
  value,
  onChange,
  disabled,
  caregiver,
}: {
  lang: KioskLang;
  value: PatientDetails;
  onChange: (next: PatientDetails) => void;
  disabled: boolean;
  caregiver: boolean;
}) {
  const set = (patch: Partial<PatientDetails>) => onChange({ ...value, ...patch });

  return (
    <div className={s.detailsForm}>
      <label className={`${s.field} ${s.fieldWide}`}>
        <span className={s.fieldLabel}>
          <span className={s.fieldIcon} aria-hidden="true">
            <Icon name="user" />
          </span>
          {t(caregiver ? "nameInput" : "yourNameTitle", lang)}
          <em className={s.fieldRequired}>{t("requiredLabel", lang)}</em>
        </span>
        <input
          className={s.fieldInput}
          value={value.name}
          disabled={disabled}
          maxLength={200}
          autoComplete="off"
          autoCapitalize="words"
          spellCheck={false}
          placeholder={t("nameInput", lang)}
          onChange={(e) => set({ name: e.target.value })}
          data-testid="patient-name"
        />
      </label>

      <label className={s.field}>
        <span className={s.fieldLabel}>
          <span className={s.fieldIcon} aria-hidden="true">
            <Icon name="calendar" />
          </span>
          {t("ageInput", lang)}
          <em className={s.fieldOptional}>{t("optionalLabel", lang)}</em>
        </span>
        <input
          className={s.fieldInput}
          value={value.age == null ? "" : String(value.age)}
          disabled={disabled}
          inputMode="numeric"
          pattern="[0-9]*"
          maxLength={3}
          placeholder="—"
          onChange={(e) => set({ age: parseAge(e.target.value) })}
          data-testid="patient-age"
        />
      </label>

      <fieldset className={s.field}>
        <legend className={s.fieldLabel}>
          <span className={s.fieldIcon} aria-hidden="true">
            <Icon name="body" />
          </span>
          {t("sexInput", lang)}
          <em className={s.fieldOptional}>{t("optionalLabel", lang)}</em>
        </legend>
        <div className={s.chipRow}>
          {SEX_CHOICES.map((choice) => (
            <button
              key={choice.id}
              type="button"
              disabled={disabled}
              className={`${s.chipBtn} ${value.sex === choice.id ? s.chipBtnOn : ""}`}
              aria-pressed={value.sex === choice.id}
              // Tapping the chosen one again clears it: a patient who mis-tapped
              // must be able to take it back without restarting the intake.
              onClick={() => set({ sex: value.sex === choice.id ? null : choice.id })}
              data-testid={`patient-sex-${choice.id}`}
            >
              {t(choice.label, lang)}
            </button>
          ))}
        </div>
      </fieldset>

      <label className={`${s.field} ${s.fieldWide}`}>
        <span className={s.fieldLabel}>
          <span className={s.fieldIcon} aria-hidden="true">
            <Icon name="phone" />
          </span>
          {t("phoneInput", lang)}
          <em className={s.fieldOptional}>{t("optionalLabel", lang)}</em>
        </span>
        <input
          className={s.fieldInput}
          value={value.phone}
          disabled={disabled}
          inputMode="tel"
          maxLength={14}
          autoComplete="off"
          placeholder={t("phoneHint", lang)}
          onChange={(e) => set({ phone: e.target.value.replace(/[^\d+ ]/g, "") })}
          data-testid="patient-phone"
        />
      </label>
    </div>
  );
}

/** Digits only, and only a believable age. An unparseable box reads as "not
 *  given" rather than as zero — nobody at an OPD desk is zero years old. */
function parseAge(raw: string): number | null {
  const digits = raw.replace(/\D/g, "").slice(0, 3);
  if (!digits) return null;
  const age = Number(digits);
  if (!Number.isFinite(age) || age < 0 || age > 120) return null;
  return age;
}
