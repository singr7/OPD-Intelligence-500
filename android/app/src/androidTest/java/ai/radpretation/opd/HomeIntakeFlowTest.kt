package ai.radpretation.opd

import android.Manifest
import ai.radpretation.opd.ui.theme.OpdTheme
import ai.radpretation.opd.ui.screens.IntakeScreen
import ai.radpretation.opd.ui.screens.SignInScreen
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.rule.GrantPermissionRule
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The whole home-intake flow on an emulator (doc 06's S16 AC).
 *
 * It drives the real screens — sign in with an OTP, speak/type a complaint, walk
 * the questions, confirm the read-back — against a scripted backend. The backend
 * being scripted is deliberate: the *contract* is proven by the 932 backend
 * tests, while what only a device can prove is that these screens, with real
 * Compose recomposition and a real navigation stack, complete the flow and end
 * on "no token yet — show this on arrival".
 *
 * The typed fallback is used rather than the microphone, because the emulator
 * has no speech recogniser and doc 04 law 8 promises the typed path is always
 * there. That promise is what this test exercises.
 */
@RunWith(AndroidJUnit4::class)
class HomeIntakeFlowTest {

    /**
     * Granted up front, and this is not incidental: the intake screen asks for
     * the microphone the moment it appears, and a permission dialog puts the
     * test's own activity in the background — the composition vanishes and the
     * flow fails for a reason that has nothing to do with the flow.
     */
    @get:Rule(order = 0)
    val permissions: GrantPermissionRule = GrantPermissionRule.grant(Manifest.permission.RECORD_AUDIO)

    @get:Rule(order = 1)
    val compose = createComposeRule()

    private lateinit var server: MockWebServer
    private lateinit var container: AppContainer

    @Before
    fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when {
                request.path == "/auth/patient/otp/request" ->
                    MockResponse().setBody("""{"sent": true, "expires_at": null, "debug_code": "123456"}""")

                request.path == "/auth/patient/otp/verify" -> MockResponse().setBody(
                    """{"access_token":"a","refresh_token":"r","patient_id":"p1","via":"self",
                        "profiles":[{"patient_id":"p1","name":"Kamla Devi","via":"self"}]}""",
                )

                request.path == "/patient/intake/start" -> MockResponse().setBody(
                    """{"status":"routed","session_id":"s1","visit_id":"v1","tier":"prerecorded",
                        "department":{"key":"MEDONC","name":"Medical Oncology"},
                        "tree_key":"med_onc_new_patient@v1",
                        "node":{"id":"n1","type":"single","text":"दर्द कहाँ है?",
                                "options":[{"id":"pet","label":"पेट में"},{"id":"seena","label":"सीने में"}]},
                        "complete":false}""",
                )

                request.path == "/patient/intake/s1/answer" -> MockResponse().setBody(
                    """{"ok":true,"node_id":"n1","complete":true,"node":null}""",
                )

                request.path == "/patient/intake/s1/finish" -> MockResponse().setBody(
                    """{"readback":"आपने कहा: पेट में दर्द, तीन दिन से।","summary_md":null,"complete":true}""",
                )

                request.path == "/patient/intake/s1/confirm" -> MockResponse().setBody(
                    """{"visit_id":"v1","department":{"key":"MEDONC","name":"Medical Oncology"},
                        "red_flags":[],"token_no":null,
                        "message":"Show this on arrival — your token is issued when you check in."}""",
                )

                request.path?.startsWith("/patient/file") == true -> MockResponse().setBody(
                    """{"patient":{"patient_id":"p1","name":"Kamla Devi","lang":"hi","mrn":"M1",
                        "via":"self","hospital":"Alwar Cancer Centre"},"revision":null,"entries":[]}""",
                )

                else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not scripted"}""")
            }
        }
        server.start()

        val context = InstrumentationRegistry.getInstrumentation().targetContext
        container = AppContainer(context, server.url("/").toString())
        runBlocking { container.tokens.clear() }
    }

    @After
    fun tearDown() {
        runCatching { server.shutdown() }
        runBlocking { container.tokens.clear() }
    }

    @Test
    fun aPatientSignsInAndFinishesTomorrowsIntakeTonight() {
        // One composition for the whole flow, gated on the session exactly the
        // way `OpdRoot` gates it — the sign-in screen is a state, not a route.
        compose.setContent {
            OpdTheme {
                val signedIn by container.tokens.signedIn.collectAsState(initial = false)
                if (signedIn) IntakeScreen(container, onDone = {}) else SignInScreen(container)
            }
        }

        compose.onNodeWithTag("phone_field").performTextInput("+915551900001")
        compose.onNodeWithTag("send_code").performClick()
        compose.waitUntil(5_000) {
            compose.onAllNodesWithTagOrEmpty("code_field").isNotEmpty()
        }
        compose.onNodeWithTag("code_field").performTextInput("123456")
        compose.onNodeWithTag("verify_code").performClick()
        // Signed in — the composition swaps itself over to the intake.
        compose.waitUntil(10_000) {
            compose.onAllNodesWithTagOrEmpty("intake_text").isNotEmpty()
        }

        compose.onNodeWithTag("intake_text").performTextInput("पेट में दर्द")
        compose.onNodeWithTag("intake_begin").performClick()

        compose.waitUntil(5_000) {
            compose.onAllNodesWithTagOrEmpty("option_pet").isNotEmpty()
        }
        compose.onNodeWithTag("intake_question").assertIsDisplayed()
        compose.onNodeWithTag("option_pet").performClick()

        // The tree completed, so the app asked for the read-back on its own.
        compose.waitUntil(5_000) {
            compose.onAllNodesWithTagOrEmpty("intake_confirm").isNotEmpty()
        }
        compose.onNodeWithTag("intake_confirm").performClick()

        compose.waitUntil(5_000) {
            compose.onAllNodesWithTagOrEmpty("intake_done").isNotEmpty()
        }
        // The screen she shows at the desk tomorrow — and no token on it, which
        // is the point of doing the intake at home the night before.
        compose.onNodeWithTag("intake_done").assertIsDisplayed()
    }
}

/** `onAllNodes(...).fetchSemanticsNodes()` reads better as a list. */
private fun androidx.compose.ui.test.junit4.ComposeContentTestRule.onAllNodesWithTagOrEmpty(
    tag: String,
) = onAllNodes(androidx.compose.ui.test.hasTestTag(tag)).fetchSemanticsNodes()
