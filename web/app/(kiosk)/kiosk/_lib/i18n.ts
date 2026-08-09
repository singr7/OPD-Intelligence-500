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
  // -- registration details screen (S-UX.6) ----------------------------------
  // One screen, four facts. They travel to the token slip, the queue, the doctor
  // console and the prescription, so they are asked once and typed, not spoken:
  // a misheard name on a prescription is a different patient.
  detailsTitle: {
    hi: "मरीज़ का विवरण भरिए",
    en: "Patient details",
    mr: "रुग्णाची माहिती भरा",
    te: "రోగి వివరాలు",
  } as Str,
  detailsHint: {
    hi: "यह विवरण आपकी पर्ची और डॉक्टर की स्क्रीन पर दिखेगा।",
    en: "These details appear on your slip and on the doctor's screen.",
    mr: "ही माहिती तुमच्या पावतीवर आणि डॉक्टरांच्या स्क्रीनवर दिसेल.",
    te: "ఈ వివరాలు మీ స్లిప్‌లో మరియు డాక్టర్ స్క్రీన్‌పై కనిపిస్తాయి.",
  } as Str,
  ageInput: {
    hi: "उम्र (साल)",
    en: "Age (years)",
    mr: "वय (वर्षे)",
    te: "వయస్సు (సంవత్సరాలు)",
  } as Str,
  sexInput: {
    hi: "लिंग",
    en: "Gender",
    mr: "लिंग",
    te: "లింగం",
  } as Str,
  sexMale: {
    hi: "पुरुष",
    en: "Male",
    mr: "पुरुष",
    te: "పురుషుడు",
  } as Str,
  sexFemale: {
    hi: "महिला",
    en: "Female",
    mr: "महिला",
    te: "స్త్రీ",
  } as Str,
  sexOther: {
    hi: "अन्य",
    en: "Other",
    mr: "इतर",
    te: "ఇతర",
  } as Str,
  phoneInput: {
    hi: "मोबाइल नंबर",
    en: "Mobile number",
    mr: "मोबाइल नंबर",
    te: "మొబైల్ నంబర్",
  } as Str,
  optionalLabel: {
    hi: "ज़रूरी नहीं",
    en: "Optional",
    mr: "ऐच्छिक",
    te: "ఐచ్ఛికం",
  } as Str,
  requiredLabel: {
    hi: "ज़रूरी",
    en: "Required",
    mr: "आवश्यक",
    te: "అవసరం",
  } as Str,
  phoneHint: {
    hi: "10 अंक — रिपोर्ट और याद दिलाने के लिए",
    en: "10 digits — for reports and reminders",
    mr: "१० अंक — अहवाल आणि आठवणीसाठी",
    te: "10 అంకెలు — నివేదికలు మరియు రిమైండర్‌ల కోసం",
  } as Str,
  summaryAge: {
    hi: "उम्र / लिंग",
    en: "Age / gender",
    mr: "वय / लिंग",
    te: "వయస్సు / లింగం",
  } as Str,
  summaryPhone: {
    hi: "मोबाइल",
    en: "Mobile",
    mr: "मोबाइल",
    te: "మొబైల్",
  } as Str,
  yearsShort: {
    hi: "साल",
    en: "y",
    mr: "वर्षे",
    te: "సం",
  } as Str,
  stepProgress: {
    hi: "चरण {n} / {total}",
    en: "Step {n} of {total}",
    mr: "टप्पा {n} / {total}",
    te: "దశ {n} / {total}",
  } as Str,
  questionProgress: {
    hi: "सवाल {n} / {total}",
    en: "Question {n} of {total}",
    mr: "प्रश्न {n} / {total}",
    te: "ప్రశ్న {n} / {total}",
  } as Str,
  questionsLeft: {
    hi: "{n} सवाल बाकी",
    en: "{n} questions left",
    mr: "{n} प्रश्न बाकी",
    te: "{n} ప్రశ్నలు మిగిలాయి",
  } as Str,
  lastQuestion: {
    hi: "आख़िरी सवाल",
    en: "Last question",
    mr: "शेवटचा प्रश्न",
    te: "చివరి ప్రశ్న",
  } as Str,
  reviewStep: {
    hi: "जाँच लीजिए",
    en: "Check your answers",
    mr: "तपासून घ्या",
    te: "మీ సమాధానాలు చూడండి",
  } as Str,
  chooseOne: {
    hi: "एक चुनिए",
    en: "Choose one",
    mr: "एक निवडा",
    te: "ఒకటి ఎంచుకోండి",
  } as Str,
  chooseAny: {
    hi: "जो-जो लागू हो, चुनिए",
    en: "Choose everything that applies",
    mr: "जे लागू होईल ते निवडा",
    te: "వర్తించే అన్నింటినీ ఎంచుకోండి",
  } as Str,
  answersTitle: {
    hi: "आपके जवाब",
    en: "Your answers",
    mr: "तुमची उत्तरे",
    te: "మీ సమాధానాలు",
  } as Str,
  clear: {
    hi: "मिटाएँ",
    en: "Clear",
    mr: "पुसा",
    te: "తొలగించండి",
  } as Str,
  liveSummary: {
    hi: "अब तक की जानकारी",
    en: "Your answers so far",
    mr: "आतापर्यंतची माहिती",
    te: "ఇప్పటివరకు మీ సమాచారం",
  } as Str,
  summaryPatient: {
    hi: "मरीज़",
    en: "Patient",
    mr: "रुग्ण",
    te: "రోగి",
  } as Str,
  summaryConcern: {
    hi: "मुख्य तकलीफ़",
    en: "Main concern",
    mr: "मुख्य त्रास",
    te: "ప్రధాన సమస్య",
  } as Str,
  summaryDepartment: {
    hi: "विभाग",
    en: "Department",
    mr: "विभाग",
    te: "విభాగం",
  } as Str,
  summaryDuration: {
    hi: "कितने समय से",
    en: "Duration",
    mr: "किती काळापासून",
    te: "ఎంత కాలంగా",
  } as Str,
  summarySymptoms: {
    hi: "दूसरी बातें",
    en: "Symptoms",
    mr: "इतर माहिती",
    te: "ఇతర వివరాలు",
  } as Str,
  notAnswered: {
    hi: "अभी जवाब नहीं दिया",
    en: "Not answered yet",
    mr: "अजून उत्तर दिले नाही",
    te: "ఇంకా సమాధానం ఇవ్వలేదు",
  } as Str,
  moreAnswers: {
    hi: "{n} और जवाब",
    en: "{n} more answers",
    mr: "आणखी {n} उत्तरे",
    te: "మరో {n} సమాధానాలు",
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
  // The kiosk reads the options aloud after the question (S-UX.6): a patient who
  // cannot read the screen still hears every choice before being asked to tap.
  optionsSpokenIntro: {
    hi: "आप चुन सकते हैं:",
    en: "You can choose:",
    mr: "तुम्ही निवडू शकता:",
    te: "మీరు ఎంచుకోవచ్చు:",
  } as Str,
  optionsSpokenJoin: {
    hi: ", या ",
    en: ", or ",
    mr: ", किंवा ",
    te: ", లేదా ",
  } as Str,
  scaleSpoken: {
    hi: "शून्य से दस के बीच चुनिए।",
    en: "Choose a number between zero and ten.",
    mr: "शून्य ते दहा दरम्यान निवडा.",
    te: "సున్నా నుండి పది మధ్య ఎంచుకోండి.",
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
  // The boarding pass (doc 23 §7). The button says *pass* rather than *slip*
  // because what comes out is a different object: a fixed-size document with
  // the patient's own answers on it and a stub the desk tears off.
  printPass: {
    hi: "पास छापें",
    en: "Print pass",
    mr: "पास छापा",
    te: "పాస్ ముద్రించండి",
  } as Str,
  reprintPass: {
    hi: "फिर से छापें",
    en: "Re-print",
    mr: "पुन्हा छापा",
    te: "మళ్లీ ముద్రించండి",
  } as Str,
  /** Named for what the patient is looking at, not for the technology: this is
   *  a picture of the piece of paper they are about to be handed. */
  passPreview: {
    hi: "आपका पास",
    en: "Your pass",
    mr: "तुमचा पास",
    te: "మీ పాస్",
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
  // -- allergies (SESSION-ALLERGY) --------------------------------------------
  //
  // The one clinical question the kiosk asks outside a department's tree, so the
  // wording lives here rather than in tree content. Two rules shape all of it:
  //
  //  * **It never uses the word "allergy" alone.** Half the patients at this
  //    site would not name a drug reaction as an "एलर्जी"; they would say a
  //    medicine "did not suit" them, or that they came out in a rash. So the
  //    question is asked the way it gets answered.
  //  * **"I don't know" is an offered answer, not a dead end.** A patient forced
  //    to choose between yes and no about her own drug history will guess, and a
  //    guessed "no" is the answer that reaches a prescribing doctor as a fact.
  allergyTitle: {
    hi: "क्या कोई दवा आपको नुक़सान करती है?",
    en: "Does any medicine disagree with you?",
    mr: "कोणतं औषध तुम्हाला त्रास देतं का?",
    te: "ఏదైనా మందు మీకు పడదా?",
  } as Str,
  allergyHelp: {
    hi: "जैसे — कोई दवा खाने पर चकत्ते, सूजन, साँस लेने में तकलीफ़।",
    en: "For example — a rash, swelling, or trouble breathing after a medicine.",
    mr: "उदाहरणार्थ — औषध घेतल्यावर पुरळ, सूज किंवा श्वास घ्यायला त्रास.",
    te: "ఉదాహరణకు — మందు వాడిన తర్వాత దద్దుర్లు, వాపు లేదా ఊపిరి ఇబ్బంది.",
  } as Str,
  allergyYes: {
    hi: "हाँ, है",
    en: "Yes, there is",
    mr: "होय, आहे",
    te: "అవును, ఉంది",
  } as Str,
  allergyNo: {
    hi: "नहीं, कोई नहीं",
    en: "No, none",
    mr: "नाही, काही नाही",
    te: "లేదు, ఏదీ లేదు",
  } as Str,
  allergyUnsure: {
    hi: "मुझे पता नहीं",
    en: "I don't know",
    mr: "मला माहीत नाही",
    te: "నాకు తెలియదు",
  } as Str,
  allergyWhichTitle: {
    hi: "कौन-सी दवा?",
    en: "Which medicine?",
    mr: "कोणतं औषध?",
    te: "ఏ మందు?",
  } as Str,
  allergyWhichHelp: {
    hi: "जितना याद है उतना ही बताइए — नाम पूरा ठीक न हो तो भी चलेगा।",
    en: "Just as much as you remember — the exact name doesn't matter.",
    mr: "जेवढं आठवतं तेवढंच सांगा — नाव अगदी बरोबर नसलं तरी चालेल.",
    te: "గుర్తున్నంత చెప్పండి — పేరు సరిగ్గా లేకపోయినా పర్వాలేదు.",
  } as Str,
  allergyPlaceholder: {
    hi: "दवा का नाम",
    en: "Medicine name",
    mr: "औषधाचं नाव",
    te: "మందు పేరు",
  } as Str,
  allergyAddAnother: {
    hi: "एक और जोड़िए",
    en: "Add another",
    mr: "आणखी एक जोडा",
    te: "మరొకటి జోడించండి",
  } as Str,
  // Shown when the answer could not be saved (a server session that lost the
  // network). It tells her what to do about it rather than what failed — doc 04
  // law 8 — because the thing that matters is that the doctor still hears it.
  allergyNotSaved: {
    hi: "यह सहेजा नहीं जा सका — कृपया डॉक्टर को ख़ुद बता दीजिए।",
    en: "We couldn't save that — please tell the doctor yourself.",
    mr: "हे साठवता आलं नाही — कृपया डॉक्टरांना स्वतः सांगा.",
    te: "ఇది భద్రపరచలేకపోయాం — దయచేసి డాక్టర్‌కు మీరే చెప్పండి.",
  } as Str,
  // The rail's label for this step.
  allergyStep: {
    hi: "दवा की तकलीफ़",
    en: "Medicine reactions",
    mr: "औषधाचा त्रास",
    te: "మందుల ఇబ్బంది",
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

// -- AR3: arrival identity + the staff strip ----------------------------------
//
// **English and Hindi only, deliberately.** This copy ships in the pilot's two
// spoken languages and is *not* machine-drafted into Marathi and Telugu the way
// the block above was. These are patient-facing screens that ask for a phone
// number and a health ID — the two strings a patient is most likely to act on
// wrongly if the translation is off — and a wrong Telugu sentence about "your
// old file" is worse than an English one the patient asks a human to read.
//
// The gap is logged as pending in HANDOFF.md and STATE.md per doc 07 §4. When a
// native reviewer supplies mr/te, these keys move into `T` above and `tb`
// disappears; the type below is what makes that a compile-time move rather than
// a search.
type Bi = { hi: string; en: string };

export const T2 = {
  returningTitle: {
    hi: "क्या आप पहले यहाँ आ चुके हैं?",
    en: "Have you visited us before?",
  } as Bi,
  returningHint: {
    hi: "अगर आ चुके हैं तो हम आपकी पुरानी फ़ाइल ढूँढ़ सकते हैं। न याद हो तो भी कोई बात नहीं।",
    en: "If you have, we can look for your old file. If you're not sure, that's fine too.",
  } as Bi,
  returningYes: {
    hi: "हाँ, आ चुके हैं",
    en: "Yes, I've been here",
  } as Bi,
  returningNo: {
    hi: "नहीं, पहली बार",
    en: "No, first time",
  } as Bi,
  arrivalPhoneTitle: {
    hi: "आपका मोबाइल नंबर",
    en: "Your phone number",
  } as Bi,
  arrivalPhoneHint: {
    hi: "वही नंबर जो पिछली बार दिया था। न देना चाहें तो छोड़ दीजिए।",
    en: "The number you gave us last time. You can skip it if you'd rather not.",
  } as Bi,
  arrivalIdTitle: {
    hi: "क्या आपके पास अस्पताल का पहचान नंबर है?",
    en: "Do you have a hospital ID number?",
  } as Bi,
  arrivalIdHint: {
    hi: "यह आपकी पुरानी पर्ची या कार्ड पर लिखा होता है। न हो तो छोड़ दीजिए।",
    en: "It's printed on your old slip or card. Skip this if you don't have one.",
  } as Bi,
  arrivalIdInput: {
    hi: "पहचान नंबर",
    en: "Hospital ID",
  } as Bi,
  skipThis: {
    hi: "छोड़ दीजिए",
    en: "Skip this",
  } as Bi,
  // Shown once the patient has given a phone or an ID — and shown *whether or
  // not* anything matched, which is the point. The kiosk is a public terminal;
  // a line that appears only on a hit tells whoever is standing behind the
  // patient that this hospital has a file on them. See app/assignment.py.
  arrivalAck: {
    hi: "धन्यवाद — शायद आपकी फ़ाइल हमारे पास पहले से है। हमारे कर्मचारी अभी इसकी पुष्टि करेंगे।",
    en: "Thank you — we may already have your file. Our staff will confirm it in a moment.",
  } as Bi,
  keypadDelete: {
    hi: "मिटाएँ",
    en: "Delete",
  } as Bi,
  // -- the staff strip (coordinator-facing, on the patient's token screen) ----
  staffTitle: {
    hi: "कर्मचारी",
    en: "Staff",
  } as Bi,
  staffUnlock: {
    hi: "खोलिए",
    en: "Unlock",
  } as Bi,
  staffLock: {
    hi: "बंद कीजिए",
    en: "Lock",
  } as Bi,
  staffLocked: {
    hi: "यह हिस्सा कर्मचारियों के लिए है।",
    en: "This section is for staff.",
  } as Bi,
  staffWhoAreYou: {
    hi: "आप कौन हैं?",
    en: "Who are you?",
  } as Bi,
  staffEnterPin: {
    hi: "अपना पिन डालिए",
    en: "Enter your PIN",
  } as Bi,
  staffWrongPin: {
    hi: "यह पिन सही नहीं है।",
    en: "That PIN was not recognised.",
  } as Bi,
  staffNoHolders: {
    hi: "किसी कर्मचारी का पिन सेट नहीं है।",
    en: "No staff PIN has been set on this kiosk.",
  } as Bi,
  staffCandidate: {
    hi: "शायद पुरानी फ़ाइल",
    en: "Possible existing file",
  } as Bi,
  staffNoCandidate: {
    hi: "कोई पुरानी फ़ाइल नहीं मिली — नई फ़ाइल बनेगी।",
    en: "No prior file matched — this arrival stays a new file.",
  } as Bi,
  staffSamePerson: {
    hi: "यही व्यक्ति — जोड़िए",
    en: "Same person — link",
  } as Bi,
  staffNotSamePerson: {
    hi: "यह अलग व्यक्ति है",
    en: "Not the same person",
  } as Bi,
  staffLinked: {
    hi: "फ़ाइल जोड़ दी गई",
    en: "File linked",
  } as Bi,
  staffRejected: {
    hi: "अलग व्यक्ति — नई फ़ाइल",
    en: "Different person — new file",
  } as Bi,
  staffLastVisit: {
    hi: "पिछली बार",
    en: "Last visit",
  } as Bi,
  staffDepartment: {
    hi: "विभाग",
    en: "Department",
  } as Bi,
  staffDoctor: {
    hi: "डॉक्टर",
    en: "Doctor",
  } as Bi,
  staffNoDoctor: {
    hi: "कोई नहीं — विभाग की सूची में",
    en: "Nobody yet — leave in the department pool",
  } as Bi,
  staffOnDuty: {
    hi: "आज ड्यूटी पर",
    en: "On duty today",
  } as Bi,
  staffOffDuty: {
    hi: "आज ड्यूटी पर नहीं",
    en: "Not rostered today",
  } as Bi,
  staffNoDoctors: {
    hi: "इस विभाग में कोई डॉक्टर दर्ज नहीं है।",
    en: "No doctors are on record for this department.",
  } as Bi,
  staffSkip: {
    hi: "अभी छोड़िए",
    en: "Skip",
  } as Bi,
  staffConfirm: {
    hi: "पक्का कीजिए",
    en: "Confirm",
  } as Bi,
  staffSkipHint: {
    hi: "छोड़ने पर मरीज़ विभाग की सूची में रहेगा — डेस्क से बाद में दिया जा सकता है।",
    en: "Skipping leaves the patient in the department pool — the desk can assign them later.",
  } as Bi,
  staffAssigned: {
    hi: "हो गया",
    en: "Done",
  } as Bi,
  staffChanging: {
    hi: "सहेज रहे हैं…",
    en: "Saving…",
  } as Bi,
  // A department change reissues the token. The patient is holding a printed
  // slip with the old number on it, so this is shouted, not noted.
  staffNewToken: {
    hi: "नया टोकन नंबर — मरीज़ को यही बताइए",
    en: "New token number — hand this to the patient",
  } as Bi,
  staffOldToken: {
    hi: "पुरानी पर्ची का नंबर {n} अब नहीं चलेगा",
    en: "Their printed slip says {n} — that number is no longer valid",
  } as Bi,
  staffTokenAck: {
    hi: "मैंने मरीज़ को बता दिया",
    en: "I've told the patient",
  } as Bi,
} as const;

/** Kiosk copy that exists in Hindi and English only (see the note above).
 *  A Marathi or Telugu kiosk falls through to English rather than to a
 *  machine translation nobody has read. */
export function tb(key: keyof typeof T2, lang: KioskLang): string {
  return lang === "hi" ? T2[key].hi : T2[key].en;
}
