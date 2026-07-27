// Kiosk UI strings (doc 04 §2 law 7: warm, second person, plain — never clinical
// to a patient). All four pilot languages (S13); the type below makes a missing
// language a compile error, which is the web-side completeness gate the backend's
// `app.lang_qa` harness is for the tree/template/read-back text. The *tree*
// questions come from the backend already in the patient's language; these are the
// shell. mr/te are model-drafted, pending native review at S21.

export type KioskLang = "hi" | "en" | "mr" | "te";

export const KIOSK_LANGS: { code: KioskLang; label: string }[] = [
  // Each labelled in its own script (doc 04 law 5).
  { code: "hi", label: "हिंदी" },
  { code: "mr", label: "मराठी" },
  { code: "te", label: "తెలుగు" },
  { code: "en", label: "English" },
];

type Str = Record<KioskLang, string>;

export const T = {
  hospital: {
    hi: "राजकीय कैंसर अस्पताल, अलवर",
    en: "Government Cancer Hospital, Alwar",
    mr: "शासकीय कर्करोग रुग्णालय, अलवर",
    te: "ప్రభుత్వ క్యాన్సర్ ఆసుపత్రి, అల్వర్",
  } as Str,
  trust: {
    hi: "आपकी बातें सिर्फ़ आपके डॉक्टर तक जाती हैं।",
    en: "Your answers go only to your doctor.",
    mr: "तुमच्या गोष्टी फक्त तुमच्या डॉक्टरांपर्यंतच जातात.",
    te: "మీ సమాధానాలు మీ డాక్టర్‌కు మాత్రమే చేరుతాయి.",
  } as Str,
  chooseLanguage: {
    hi: "अपनी भाषा चुनिए",
    en: "Choose your language",
    mr: "तुमची भाषा निवडा",
    te: "మీ భాషను ఎంచుకోండి",
  } as Str,
  tapToBegin: {
    hi: "शुरू करने के लिए छुएँ",
    en: "Tap to begin",
    mr: "सुरू करण्यासाठी स्पर्श करा",
    te: "ప్రారంభించడానికి తాకండి",
  } as Str,
  caregiverTitle: {
    hi: "क्या आप किसी और के लिए जवाब दे रहे हैं?",
    en: "Are you answering for someone else?",
    mr: "तुम्ही दुसऱ्या कुणासाठी उत्तर देत आहात का?",
    te: "మీరు మరొకరి కోసం సమాధానం ఇస్తున్నారా?",
  } as Str,
  caregiverHelp: {
    hi: "कोई बात नहीं — बस हमें बता दीजिए।",
    en: "That's completely fine — just let us know.",
    mr: "काही हरकत नाही — फक्त आम्हाला सांगा.",
    te: "ఏం ఫర్వాలేదు — మాకు చెప్పండి చాలు.",
  } as Str,
  itsForMe: {
    hi: "मैं अपने लिए",
    en: "For myself",
    mr: "माझ्यासाठी",
    te: "నా కోసం",
  } as Str,
  itsForSomeone: {
    hi: "किसी और के लिए",
    en: "For someone else",
    mr: "दुसऱ्या कुणासाठी",
    te: "మరొకరి కోసం",
  } as Str,
  patientNameTitle: {
    hi: "मरीज़ का नाम क्या है?",
    en: "What is the patient's name?",
    mr: "रुग्णाचे नाव काय आहे?",
    te: "రోగి పేరు ఏమిటి?",
  } as Str,
  yourNameTitle: {
    hi: "आपका नाम क्या है?",
    en: "What is your name?",
    mr: "तुमचे नाव काय आहे?",
    te: "మీ పేరు ఏమిటి?",
  } as Str,
  nameHint: {
    hi: "जैसा नाम लिखा जाता है, वैसा बोलिए या टाइप कीजिए।",
    en: "Say or type the name exactly as it is written.",
    mr: "नाव जसे लिहिले जाते तसे बोला किंवा टाइप करा.",
    te: "పేరు ఎలా వ్రాస్తారో అలాగే చెప్పండి లేదా టైప్ చేయండి.",
  } as Str,
  nameInput: {
    hi: "मरीज़ का नाम",
    en: "Patient name",
    mr: "रुग्णाचे नाव",
    te: "రోగి పేరు",
  } as Str,
  clear: {
    hi: "मिटाएँ",
    en: "Clear",
    mr: "पुसा",
    te: "తొలగించండి",
  } as Str,
  ccTitle: {
    hi: "आज आप क्यों आए हैं?",
    en: "What brings you in today?",
    mr: "आज तुम्ही का आला आहात?",
    te: "ఈ రోజు మీరు ఎందుకు వచ్చారు?",
  } as Str,
  ccHint: {
    hi: "बड़े बटन को दबाकर आराम से बोलिए। कोई जल्दी नहीं है।",
    en: "Press the button and speak in your own words. There's no hurry.",
    mr: "मोठं बटण दाबून आरामात बोला. काही घाई नाही.",
    te: "పెద్ద బటన్ నొక్కి మీ మాటల్లో చెప్పండి. తొందర ఏమీ లేదు.",
  } as Str,
  listening: {
    hi: "सुन रहे हैं…",
    en: "Listening…",
    mr: "ऐकत आहोत…",
    te: "వింటున్నాము…",
  } as Str,
  transcribing: {
    hi: "समझ रहे हैं…",
    en: "Understanding…",
    mr: "समजून घेत आहोत…",
    te: "అర్థం చేసుకుంటున్నాము…",
  } as Str,
  tapToSpeak: {
    hi: "बोलने के लिए दबाइए",
    en: "Press to speak",
    mr: "बोलण्यासाठी दाबा",
    te: "మాట్లాడటానికి నొక్కండి",
  } as Str,
  typeInstead: {
    hi: "टाइप करके बताइए",
    en: "Type it instead",
    mr: "त्याऐवजी टाइप करा",
    te: "బదులుగా టైప్ చేయండి",
  } as Str,
  useServerStt: {
    hi: "साफ़ नहीं सुनाई दिया? सर्वर से सुनवाएँ",
    en: "Trouble hearing? Use server speech",
    mr: "स्पष्ट ऐकू आलं नाही? सर्व्हरवरून ऐकवा",
    te: "సరిగ్గా వినిపించలేదా? సర్వర్ ద్వారా వినిపించండి",
  } as Str,
  youSaid: {
    hi: "आपने कहा:",
    en: "You said:",
    mr: "तुम्ही म्हणालात:",
    te: "మీరు చెప్పారు:",
  } as Str,
  // Adaptive intake (S-ADAPT.1, doc 11): the "answer by voice" affordance that
  // sits alongside the taps — taps stay first-class (doc 04 law 8).
  answerByVoice: {
    hi: "या बोलकर जवाब दीजिए",
    en: "Or answer by voice",
    mr: "किंवा बोलून उत्तर द्या",
    te: "లేదా మాట్లాడి సమాధానం ఇవ్వండి",
  } as Str,
  orTapAnswer: {
    hi: "या नीचे से चुनिए",
    en: "Or tap your answer below",
    mr: "किंवा खालून निवडा",
    te: "లేదా కింద నుండి ఎంచుకోండి",
  } as Str,
  sttFailed: {
    hi: "माफ़ कीजिए, ठीक से सुनाई नहीं दिया — एक बार फिर बोलिए।",
    en: "I couldn't hear that properly — let's try once more.",
    mr: "माफ करा, नीट ऐकू आलं नाही — पुन्हा एकदा बोला.",
    te: "క్షమించండి, సరిగ్గా వినిపించలేదు — మరోసారి చెప్పండి.",
  } as Str,
  chooseDept: {
    hi: "सही डॉक्टर तक पहुँचाने में हमारी मदद कीजिए",
    en: "Help us send you to the right doctor",
    mr: "योग्य डॉक्टरांपर्यंत पोहोचवण्यात आम्हाला मदत करा",
    te: "సరైన డాక్టర్ వద్దకు పంపడంలో మాకు సహాయం చేయండి",
  } as Str,
  callStaff: {
    hi: "मुझे मदद चाहिए",
    en: "I need help",
    mr: "मला मदत हवी आहे",
    te: "నాకు సహాయం కావాలి",
  } as Str,
  replay: {
    hi: "फिर से सुनिए",
    en: "Play again",
    mr: "पुन्हा ऐका",
    te: "మళ్లీ వినండి",
  } as Str,
  back: {
    hi: "पीछे",
    en: "Back",
    mr: "मागे",
    te: "వెనుకకు",
  } as Str,
  next: {
    hi: "आगे",
    en: "Next",
    mr: "पुढे",
    te: "తదుపరి",
  } as Str,
  ofCount: {
    hi: "में से",
    en: "of",
    mr: "पैकी",
    te: "లో",
  } as Str,
  confirmTitle: {
    hi: "यह मैंने समझा — क्या यह सही है?",
    en: "Here's what I understood — is it right?",
    mr: "मला हे समजलं — हे बरोबर आहे का?",
    te: "నేను అర్థం చేసుకున్నది ఇదీ — ఇది సరైనదేనా?",
  } as Str,
  confirmYes: {
    hi: "हाँ, सही है",
    en: "Yes, that's right",
    mr: "होय, बरोबर आहे",
    te: "అవును, సరైనదే",
  } as Str,
  confirmEdit: {
    hi: "कुछ बदलना है",
    en: "I want to change something",
    mr: "काहीतरी बदलायचं आहे",
    te: "ఏదో మార్చాలనుకుంటున్నాను",
  } as Str,
  tokenTitle: {
    hi: "आपका टोकन नंबर",
    en: "Your token number",
    mr: "तुमचा टोकन नंबर",
    te: "మీ టోకెన్ నంబర్",
  } as Str,
  tokenWait: {
    hi: "कृपया बैठिए, आपको नंबर से बुलाया जाएगा।",
    en: "Please have a seat — you'll be called by this number.",
    mr: "कृपया बसा — तुम्हाला या नंबरने बोलावलं जाईल.",
    te: "దయచేసి కూర్చోండి — ఈ నంబర్‌తో మిమ్మల్ని పిలుస్తారు.",
  } as Str,
  urgentNote: {
    hi: "हमने आपकी बात नर्स को बता दी है, वे जल्दी देखेंगी।",
    en: "We've alerted a nurse — you'll be seen sooner.",
    mr: "आम्ही नर्सला कळवलं आहे — तुम्हाला लवकर पाहिलं जाईल.",
    te: "మేము నర్సుకు తెలియజేశాము — మిమ్మల్ని త్వరగా చూస్తారు.",
  } as Str,
  done: {
    hi: "धन्यवाद",
    en: "Thank you",
    mr: "धन्यवाद",
    te: "ధన్యవాదాలు",
  } as Str,
  startOver: {
    hi: "नया शुरू करें",
    en: "Start over",
    mr: "पुन्हा नव्याने सुरू करा",
    te: "మళ్లీ మొదలుపెట్టండి",
  } as Str,
  stillThere: {
    hi: "क्या आप अभी भी यहाँ हैं?",
    en: "Are you still there?",
    mr: "तुम्ही अजूनही इथे आहात का?",
    te: "మీరు ఇంకా ఇక్కడ ఉన్నారా?",
  } as Str,
  tapToContinue: {
    hi: "जारी रखने के लिए छुएँ",
    en: "Tap to continue",
    mr: "पुढे चालू ठेवण्यासाठी स्पर्श करा",
    te: "కొనసాగించడానికి తాకండి",
  } as Str,
  none: {
    hi: "कुछ नहीं / लागू नहीं",
    en: "None / not applicable",
    mr: "काही नाही / लागू नाही",
    te: "ఏదీ లేదు / వర్తించదు",
  } as Str,
  submit: {
    hi: "यह जवाब भेजिए",
    en: "Send this answer",
    mr: "हे उत्तर पाठवा",
    te: "ఈ సమాధానాన్ని పంపండి",
  } as Str,
  // Downtime mode (S7, doc 01 §5). Reassure, don't alarm: the intake still works.
  downtimeBanner: {
    hi: "ऑफ़लाइन मोड — आपकी पर्ची और जानकारी सुरक्षित है",
    en: "Offline mode — your token and answers are safe",
    mr: "ऑफलाइन मोड — तुमची पावती आणि माहिती सुरक्षित आहे",
    te: "ఆఫ్‌లైన్ మోడ్ — మీ టోకెన్ మరియు సమాధానాలు సురక్షితం",
  } as Str,
  downtimePending: {
    hi: "{n} पर्चियाँ जुड़ने का इंतज़ार कर रही हैं",
    en: "{n} waiting to sync",
    mr: "{n} पावत्या जोडल्या जाण्याची वाट पाहत आहेत",
    te: "{n} సమకాలీకరణ కోసం వేచి ఉన్నాయి",
  } as Str,
  printSlip: {
    hi: "पर्ची छापें",
    en: "Print slip",
    mr: "पावती छापा",
    te: "స్లిప్ ముద్రించండి",
  } as Str,
  // Error micro-copy (doc 04 law 8: never blame). Previously inline hi/en
  // ternaries in KioskApp — folded here so mr/te patients see their own language.
  genericError: {
    hi: "कुछ गड़बड़ हुई — फिर कोशिश कीजिए।",
    en: "Something went wrong — please try again.",
    mr: "काहीतरी चूक झाली — पुन्हा प्रयत्न करा.",
    te: "ఏదో పొరపాటు జరిగింది — మళ్లీ ప్రయత్నించండి.",
  } as Str,
  // Spoken aloud on the token screen (doc 04 law 12: all text also as audio).
  // {n} is the token number, filled by the caller.
  tokenSpoken: {
    hi: "आपका टोकन नंबर {n}",
    en: "Your token number is {n}",
    mr: "तुमचा टोकन नंबर {n}",
    te: "మీ టోకెన్ నంబర్ {n}",
  } as Str,
  offlineDeptUnavailable: {
    hi: "यह पर्ची कर्मचारी से लें — अभी ऑफ़लाइन सेवा उपलब्ध नहीं।",
    en: "Please see the staff desk — offline service is unavailable for this department.",
    mr: "ही पावती कर्मचाऱ्यांकडून घ्या — सध्या ऑफलाइन सेवा उपलब्ध नाही.",
    te: "ఈ స్లిప్ సిబ్బంది నుండి తీసుకోండి — ప్రస్తుతం ఈ విభాగానికి ఆఫ్‌లైన్ సేవ అందుబాటులో లేదు.",
  } as Str,
} as const;

export function t(key: keyof typeof T, lang: KioskLang): string {
  return T[key][lang];
}
