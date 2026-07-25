package ai.radpretation.opd

import ai.radpretation.opd.data.ApiClient
import ai.radpretation.opd.data.PatientRepository
import ai.radpretation.opd.data.TokenPair
import ai.radpretation.opd.data.TokenStore
import ai.radpretation.opd.data.local.OpdDatabase
import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.net.HttpURLConnection

/**
 * The care file, offline (doc 03 §1c.1 — the session's first instrumented AC).
 *
 * Everything here runs against a real Room database on the device, because the
 * claim being tested is about persistence: what a patient sees after the network
 * has gone away, not what a mock returns.
 */
@RunWith(AndroidJUnit4::class)
class OfflineCareFileTest {

    private lateinit var server: MockWebServer
    private lateinit var db: OpdDatabase
    private lateinit var repo: PatientRepository
    private lateinit var tokens: TokenStore

    private val fileJson = """
        {
          "patient": {"patient_id": "p1", "name": "Kamla Devi", "lang": "hi", "mrn": "MRN000123",
                      "village": "Ramgarh", "via": "self", "hospital": "Alwar Cancer Centre"},
          "revision": "2026-07-25T10:00:00Z",
          "entries": [
            {"kind": "prescription", "id": "rx1", "visit_id": "v1", "at": "2026-07-24T10:00:00Z",
             "department": "Medical Oncology", "doctor": "A Sharma",
             "meds": [{"name": "Tab Ondansetron", "dose": "4mg", "freq": "1-0-1", "flagged": false}]},
            {"kind": "summary", "id": "in1", "visit_id": "v1", "at": "2026-07-24T09:30:00Z",
             "department": "Medical Oncology", "doctor": "A Sharma",
             "summary_md": "पेट में दर्द, तीन दिन से"}
          ]
        }
    """.trimIndent()

    @Before
    fun setUp() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        server = MockWebServer().apply { start() }
        db = Room.inMemoryDatabaseBuilder(context, OpdDatabase::class.java).build()
        tokens = TokenStore(context)
        runBlocking {
            tokens.save(TokenPair(accessToken = "access", refreshToken = "refresh", patientId = "p1"))
        }
        repo = PatientRepository(ApiClient(server.url("/").toString(), tokens), db, tokens)
    }

    @After
    fun tearDown() {
        server.shutdown()
        db.close()
        runBlocking { tokens.clear() }
    }

    @Test
    fun theFileSurvivesTheNetworkGoingAway() = runBlocking {
        server.enqueue(MockResponse().setBody(fileJson).setHeader("ETag", "W/\"1\""))
        assertTrue(repo.refreshFile())

        // The network is gone: the server is down and every call will fail.
        server.shutdown()

        val cached = db.files().entriesNow()
        assertEquals(2, cached.size)

        val patient = repo.cachedPatient.let { flow ->
            var found: ai.radpretation.opd.data.MeOut? = null
            kotlinx.coroutines.withTimeoutOrNull(2_000) {
                flow.collect { if (it != null) { found = it; return@collect } }
            }
            found
        }
        assertEquals("Kamla Devi", patient?.name)

        // And a refresh that fails does not empty it — the failure is silent and
        // the papers stay on the phone.
        runCatching { repo.refreshFile() }
        assertEquals(2, db.files().entriesNow().size)
    }

    @Test
    fun anUnchangedFileIsNotRewritten() = runBlocking {
        server.enqueue(MockResponse().setBody(fileJson).setHeader("ETag", "W/\"1\""))
        repo.refreshFile()

        server.enqueue(MockResponse().setResponseCode(HttpURLConnection.HTTP_NOT_MODIFIED))
        assertEquals(false, repo.refreshFile())
        assertEquals(2, db.files().entriesNow().size)

        // The second request carried the ETag the first one returned — which is
        // what makes opening the app on 2G cost a few hundred bytes.
        server.takeRequest()
        val conditional = server.takeRequest()
        assertEquals("W/\"1\"", conditional.getHeader("If-None-Match"))
    }
}
