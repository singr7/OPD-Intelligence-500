package ai.radpretation.opd

import ai.radpretation.opd.data.ApiClient
import ai.radpretation.opd.data.EnvironmentAllowList
import ai.radpretation.opd.data.EnvironmentMismatch
import ai.radpretation.opd.data.EnvironmentPairing
import ai.radpretation.opd.data.EnvironmentStore
import ai.radpretation.opd.data.TokenPair
import ai.radpretation.opd.data.TokenStore
import ai.radpretation.opd.data.local.OpdDatabase
import ai.radpretation.opd.intake.CrossEnvironmentSync
import ai.radpretation.opd.intake.OwnedOfflineIntakes
import android.content.Context
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import java.time.Instant

@RunWith(RobolectricTestRunner::class)
class EnvironmentPairingTest {
    private lateinit var context: Context
    private lateinit var server: MockWebServer
    private lateinit var tokens: TokenStore

    @Before
    fun setUp() {
        context = ApplicationProvider.getApplicationContext()
        server = MockWebServer()
        server.start()
        tokens = TokenStore(context)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun releaseAllowListRequiresHttpsExactHostsAndApiPath() {
        EnvironmentAllowList.parse(
            "https://omen.opd.radpretation.ai/api",
            "https://opd-cloud.radpretation.ai/api",
            allowDebug = false,
        )
        org.junit.Assert.assertThrows(IllegalArgumentException::class.java) {
            EnvironmentAllowList.parse(
                "http://omen.opd.radpretation.ai/api",
                "https://opd-cloud.radpretation.ai/api",
                false,
            )
        }
        org.junit.Assert.assertThrows(IllegalArgumentException::class.java) {
            EnvironmentAllowList.parse(
                "https://evil.example/api",
                "https://opd-cloud.radpretation.ai/api",
                false,
            )
        }
        org.junit.Assert.assertThrows(IllegalArgumentException::class.java) {
            EnvironmentAllowList.parse(
                "https://omen.opd.radpretation.ai/api",
                "https://aws.opd.radpretation.ai/api",
                false,
            )
        }
    }

    @Test
    fun switchRechecksIdentityClearsTokensAndPersistsSelection() = runTest {
        val base = server.url("/api").toString().trimEnd('/')
        val allowList = EnvironmentAllowList.parse(base, base, allowDebug = true)
        val store = EnvironmentStore(context, allowList)
        store.select("omen")
        tokens.save(TokenPair("access", "refresh", patientId = "patient"))
        repeat(2) {
            server.enqueue(MockResponse().setBody("""{"status":"ok"}"""))
            server.enqueue(
                MockResponse().setBody(
                    """{"environment_id":"aws","human_name":"AWS standby","api_contract_version":"2026-07-28","release_sha":"abc","current_time":"${Instant.now()}"}""",
                ),
            )
        }
        var cleared = false
        val api = ApiClient({ store.current().apiBase }, tokens)
        val pairing = EnvironmentPairing(store, tokens, api) { cleared = true }
        val probe = pairing.probe(allowList.require("aws"))
        pairing.confirmSwitch(probe)

        assertEquals("aws", store.current().id)
        assertEquals("aws", EnvironmentStore(context, allowList).current().id)
        assertNull(tokens.refreshToken())
        assertTrue(cleared)
        assertTrue(server.takeRequest().path!!.endsWith("/api/health"))
        assertTrue(server.takeRequest().path!!.endsWith("/api/environment"))
    }

    @Test
    fun requestsRouteToThePersistedEnvironmentAfterSwitch() = runTest {
        val awsServer = MockWebServer()
        awsServer.start()
        try {
            val allowList = EnvironmentAllowList.parse(
                server.url("/api").toString().trimEnd('/'),
                awsServer.url("/api").toString().trimEnd('/'),
                true,
            )
            val store = EnvironmentStore(context, allowList)
            val api = ApiClient({ store.current().apiBase }, tokens)
            store.select("omen")
            server.enqueue(MockResponse().setBody("""{"status":"omen"}"""))
            api.get("/route", authenticated = false)
            assertEquals("/api/route", server.takeRequest().path)

            store.select("aws")
            awsServer.enqueue(MockResponse().setBody("""{"status":"aws"}"""))
            api.get("/route", authenticated = false)
            assertEquals("/api/route", awsServer.takeRequest().path)
        } finally {
            awsServer.shutdown()
        }
    }

    @Test(expected = EnvironmentMismatch::class)
    fun excessiveClockSkewIsRejected() = runTest {
        val base = server.url("/api").toString().trimEnd('/')
        val allowList = EnvironmentAllowList.parse(base, base, true)
        val store = EnvironmentStore(context, allowList)
        server.enqueue(MockResponse().setBody("""{"status":"ok"}"""))
        server.enqueue(
            MockResponse().setBody(
                """{"environment_id":"omen","human_name":"old","api_contract_version":"2026-07-28","release_sha":"abc","current_time":"2020-01-01T00:00:00Z"}""",
            ),
        )
        EnvironmentPairing(store, tokens, ApiClient(base, tokens)) {}.probe(allowList.require("omen"))
    }

    @Test(expected = EnvironmentMismatch::class)
    fun mismatchedServerIdentityIsRejected() = runTest {
        val base = server.url("/api").toString().trimEnd('/')
        val allowList = EnvironmentAllowList.parse(base, base, true)
        val store = EnvironmentStore(context, allowList)
        server.enqueue(MockResponse().setBody("""{"status":"ok"}"""))
        server.enqueue(
            MockResponse().setBody(
                """{"environment_id":"omen","human_name":"wrong","api_contract_version":"2026-07-28","release_sha":"abc","current_time":"${Instant.now()}"}""",
            ),
        )
        EnvironmentPairing(store, tokens, ApiClient(base, tokens)) {}.probe(allowList.require("aws"))
    }

    @Test
    fun offlineIntakeCanOnlyBeReadByItsOwningEnvironment() = runTest {
        val base = server.url("/api").toString().trimEnd('/')
        val environments = EnvironmentStore(
            context,
            EnvironmentAllowList.parse(base, base, true),
        )
        environments.select("omen")
        val db = Room.inMemoryDatabaseBuilder(context, OpdDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        try {
            val owned = OwnedOfflineIntakes(db.pendingIntakes()) { environments.current().id }
            owned.save("offline-1", """{"patient_name":"not logged"}""")
            assertEquals(1, owned.pendingForCurrent().size)
            environments.select("aws")
            assertTrue(owned.pendingForCurrent().isEmpty())
            org.junit.Assert.assertThrows(CrossEnvironmentSync::class.java) {
                kotlinx.coroutines.runBlocking { owned.requireCurrent("offline-1") }
            }
        } finally {
            db.close()
        }
    }
}
