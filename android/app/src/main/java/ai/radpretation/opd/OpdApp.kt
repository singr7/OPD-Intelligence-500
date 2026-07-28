package ai.radpretation.opd

import ai.radpretation.opd.data.ApiClient
import ai.radpretation.opd.data.AuthRepository
import ai.radpretation.opd.data.PatientRepository
import ai.radpretation.opd.data.TokenStore
import ai.radpretation.opd.data.EnvironmentAllowList
import ai.radpretation.opd.data.EnvironmentPairing
import ai.radpretation.opd.data.EnvironmentStore
import ai.radpretation.opd.data.local.OpdDatabase
import ai.radpretation.opd.intake.IntakeRepository
import ai.radpretation.opd.intake.OwnedOfflineIntakes
import android.app.Application
import android.content.Context
import androidx.room.Room

/**
 * The whole dependency graph, by hand (see `gradle/libs.versions.toml` for why
 * there is no Hilt here).
 *
 * Everything is lazy, so launching to the sign-in screen does not open a
 * database, and every collaborator is constructor-injectable — which is what
 * lets the tests build a container against MockWebServer and an in-memory Room
 * without a test framework of its own.
 */
class AppContainer(
    context: Context,
    baseUrl: String? = null,
    allowList: EnvironmentAllowList = EnvironmentAllowList.fromBuildConfig(),
) {
    private val appContext = context.applicationContext

    val tokens: TokenStore by lazy { TokenStore(appContext) }

    val environments: EnvironmentStore by lazy {
        EnvironmentStore(
            appContext,
            baseUrl?.let { EnvironmentAllowList.parse(it, it, allowDebug = true) } ?: allowList,
        )
    }

    val api: ApiClient by lazy { ApiClient({ environments.current().apiBase }, tokens) }

    val db: OpdDatabase by lazy {
        Room.databaseBuilder(appContext, OpdDatabase::class.java, "opd.db")
            .addMigrations(OpdDatabase.MIGRATION_1_2)
            .build()
    }

    val patients: PatientRepository by lazy {
        PatientRepository(api, db, tokens) { environments.current().id }
    }

    val auth: AuthRepository by lazy { AuthRepository(api, tokens) }

    val intake: IntakeRepository by lazy { IntakeRepository(api) }

    val offlineIntakes: OwnedOfflineIntakes by lazy {
        OwnedOfflineIntakes(db.pendingIntakes()) { environments.current().id }
    }

    val pairing: EnvironmentPairing by lazy {
        EnvironmentPairing(environments, tokens, api) {
            db.files().clear()
            db.kv().clear()
        }
    }
}

class OpdApp : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
    }

    /**
     * Point the whole app at a different backend.
     *
     * Exists for the instrumented tests, which drive the real screens against a
     * MockWebServer on localhost. Deliberately a method on the application
     * rather than a debug-only build flavour: a second flavour would mean the
     * APK the size gate measures is not the APK the UI tests exercised.
     */
    fun useContainer(replacement: AppContainer) {
        container = replacement
    }

    companion object {
        fun containerOf(context: Context): AppContainer =
            (context.applicationContext as OpdApp).container
    }
}
