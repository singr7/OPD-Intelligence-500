package ai.radpretation.opd.ui

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import java.util.Locale

/**
 * Voice, on the device (doc 03 §1c: "native speech").
 *
 * Nothing here calls the server. The kiosk sends audio to Whisper because a
 * kiosk is a fixed terminal on hospital wifi; a phone in a village has the
 * opposite economics — Android's own recognizer and TTS are free, instant, and
 * already speak hi/mr/te, while streaming audio over 2G costs the patient money
 * and the pilot a GPU slot (S-OSS.2's contention note).
 *
 * The trade is honest and worth writing down: on-device recognition of Marathi
 * medical speech is worse than Whisper's, which is exactly why every spoken
 * answer in the intake has a tap alternative on the same screen (doc 04 law 8)
 * and why a spoken free-text complaint is re-read back before it is used.
 */

/** Reads text aloud in the patient's language. One engine per screen. */
class Speaker(context: Context, private val lang: String) {
    private var ready = false
    private var pending: String? = null

    private val tts: TextToSpeech = TextToSpeech(context.applicationContext) { status ->
        if (status == TextToSpeech.SUCCESS) {
            ready = true
            val queued = pending
            pending = null
            queued?.let { say(it) }
        }
    }

    fun say(text: String) {
        if (text.isBlank()) return
        if (!ready) {
            // Asked before the engine finished starting — remember the last
            // thing and speak it when it does. Every screen auto-plays on
            // entry (doc 04 law 1), so this races on every single screen.
            pending = text
            return
        }
        tts.language = localeOf(lang)
        tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "opd")
    }

    fun stop() {
        if (ready) tts.stop()
    }

    fun shutdown() {
        runCatching { tts.stop() }
        runCatching { tts.shutdown() }
    }

    companion object {
        /**
         * Marathi and Telugu are `mr_IN` / `te_IN` to Android; a bare "mr" often
         * resolves to nothing and the engine silently falls back to English —
         * which sounds like the app ignoring the patient's language choice.
         */
        fun localeOf(lang: String): Locale = when (lang) {
            "hi" -> Locale("hi", "IN")
            "mr" -> Locale("mr", "IN")
            "te" -> Locale("te", "IN")
            else -> Locale("en", "IN")
        }
    }
}

/** A [Speaker] bound to the composition, shut down when the screen leaves. */
@Composable
fun rememberSpeaker(lang: String): Speaker {
    val context = LocalContext.current
    val speaker = remember(lang) { Speaker(context, lang) }
    DisposableEffect(speaker) {
        onDispose { speaker.shutdown() }
    }
    return speaker
}

/**
 * One shot of speech-to-text.
 *
 * `onError` never surfaces a code to the patient — doc 04 law 8: errors never
 * blame. The caller shows "I could not hear that properly" and reveals the tap
 * alternative.
 */
class Listener(private val context: Context, private val lang: String) {
    private var recognizer: SpeechRecognizer? = null

    val available: Boolean get() = SpeechRecognizer.isRecognitionAvailable(context)

    fun start(onResult: (String) -> Unit, onError: () -> Unit) {
        if (!available) {
            onError()
            return
        }
        stop()
        val engine = SpeechRecognizer.createSpeechRecognizer(context)
        recognizer = engine
        engine.setRecognitionListener(object : RecognitionListener {
            override fun onResults(results: Bundle?) {
                val text = results
                    ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull()
                    .orEmpty()
                if (text.isBlank()) onError() else onResult(text)
            }

            override fun onError(error: Int) = onError()
            override fun onReadyForSpeech(params: Bundle?) = Unit
            override fun onBeginningOfSpeech() = Unit
            override fun onRmsChanged(rmsdB: Float) = Unit
            override fun onBufferReceived(buffer: ByteArray?) = Unit
            override fun onEndOfSpeech() = Unit
            override fun onPartialResults(partialResults: Bundle?) = Unit
            override fun onEvent(eventType: Int, params: Bundle?) = Unit
        })
        engine.startListening(
            Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(
                    RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                    RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
                )
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, Speaker.localeOf(lang).toString())
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
            },
        )
    }

    fun stop() {
        recognizer?.let {
            runCatching { it.stopListening() }
            runCatching { it.destroy() }
        }
        recognizer = null
    }
}

@Composable
fun rememberListener(lang: String): Listener {
    val context = LocalContext.current
    val listener = remember(lang) { Listener(context, lang) }
    DisposableEffect(listener) {
        onDispose { listener.stop() }
    }
    return listener
}
