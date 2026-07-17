// Kiosk UI strings (doc 04 §2 law 7: warm, second person, plain — never clinical
// to a patient). hi + en only this session; mr/te are S13. The *tree* questions
// come from the backend already in the patient's language; these are the shell.

export type KioskLang = "hi" | "en";

export const KIOSK_LANGS: { code: KioskLang; label: string }[] = [
  // Each labelled in its own script (doc 04 law 5).
  { code: "hi", label: "हिंदी" },
  { code: "en", label: "English" },
];

type Str = Record<KioskLang, string>;

export const T = {
  hospital: {
    hi: "राजकीय कैंसर अस्पताल, अलवर",
    en: "Government Cancer Hospital, Alwar",
  } as Str,
  trust: {
    hi: "आपकी बातें सिर्फ़ आपके डॉक्टर तक जाती हैं।",
    en: "Your answers go only to your doctor.",
  } as Str,
  chooseLanguage: {
    hi: "अपनी भाषा चुनिए",
    en: "Choose your language",
  } as Str,
  tapToBegin: {
    hi: "शुरू करने के लिए छुएँ",
    en: "Tap to begin",
  } as Str,
  caregiverTitle: {
    hi: "क्या आप किसी और के लिए जवाब दे रहे हैं?",
    en: "Are you answering for someone else?",
  } as Str,
  caregiverHelp: {
    hi: "कोई बात नहीं — बस हमें बता दीजिए।",
    en: "That's completely fine — just let us know.",
  } as Str,
  itsForMe: {
    hi: "मैं अपने लिए",
    en: "For myself",
  } as Str,
  itsForSomeone: {
    hi: "किसी और के लिए",
    en: "For someone else",
  } as Str,
  ccTitle: {
    hi: "आज आप क्यों आए हैं?",
    en: "What brings you in today?",
  } as Str,
  ccHint: {
    hi: "बड़े बटन को दबाकर आराम से बोलिए। कोई जल्दी नहीं है।",
    en: "Press the button and speak in your own words. There's no hurry.",
  } as Str,
  listening: {
    hi: "सुन रहे हैं…",
    en: "Listening…",
  } as Str,
  tapToSpeak: {
    hi: "बोलने के लिए दबाइए",
    en: "Press to speak",
  } as Str,
  typeInstead: {
    hi: "टाइप करके बताइए",
    en: "Type it instead",
  } as Str,
  useServerStt: {
    hi: "साफ़ नहीं सुनाई दिया? सर्वर से सुनवाएँ",
    en: "Trouble hearing? Use server speech",
  } as Str,
  youSaid: {
    hi: "आपने कहा:",
    en: "You said:",
  } as Str,
  sttFailed: {
    hi: "माफ़ कीजिए, ठीक से सुनाई नहीं दिया — एक बार फिर बोलिए।",
    en: "I couldn't hear that properly — let's try once more.",
  } as Str,
  chooseDept: {
    hi: "सही डॉक्टर तक पहुँचाने में हमारी मदद कीजिए",
    en: "Help us send you to the right doctor",
  } as Str,
  callStaff: {
    hi: "मुझे मदद चाहिए",
    en: "I need help",
  } as Str,
  replay: {
    hi: "फिर से सुनिए",
    en: "Play again",
  } as Str,
  back: {
    hi: "पीछे",
    en: "Back",
  } as Str,
  next: {
    hi: "आगे",
    en: "Next",
  } as Str,
  ofCount: {
    hi: "में से",
    en: "of",
  } as Str,
  confirmTitle: {
    hi: "यह मैंने समझा — क्या यह सही है?",
    en: "Here's what I understood — is it right?",
  } as Str,
  confirmYes: {
    hi: "हाँ, सही है",
    en: "Yes, that's right",
  } as Str,
  confirmEdit: {
    hi: "कुछ बदलना है",
    en: "I want to change something",
  } as Str,
  tokenTitle: {
    hi: "आपका टोकन नंबर",
    en: "Your token number",
  } as Str,
  tokenWait: {
    hi: "कृपया बैठिए, आपको नंबर से बुलाया जाएगा।",
    en: "Please have a seat — you'll be called by this number.",
  } as Str,
  urgentNote: {
    hi: "हमने आपकी बात नर्स को बता दी है, वे जल्दी देखेंगी।",
    en: "We've alerted a nurse — you'll be seen sooner.",
  } as Str,
  done: {
    hi: "धन्यवाद",
    en: "Thank you",
  } as Str,
  startOver: {
    hi: "नया शुरू करें",
    en: "Start over",
  } as Str,
  stillThere: {
    hi: "क्या आप अभी भी यहाँ हैं?",
    en: "Are you still there?",
  } as Str,
  tapToContinue: {
    hi: "जारी रखने के लिए छुएँ",
    en: "Tap to continue",
  } as Str,
  none: {
    hi: "कुछ नहीं / लागू नहीं",
    en: "None / not applicable",
  } as Str,
  submit: {
    hi: "यह जवाब भेजिए",
    en: "Send this answer",
  } as Str,
} as const;

export function t(key: keyof typeof T, lang: KioskLang): string {
  return T[key][lang];
}
