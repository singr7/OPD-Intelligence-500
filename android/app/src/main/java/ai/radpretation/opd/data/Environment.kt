package ai.radpretation.opd.data

import ai.radpretation.opd.BuildConfig
import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import java.net.URI
import java.time.Duration
import java.time.Instant

private val Context.environmentStore by preferencesDataStore(name = "opd_environment")

data class EnvironmentProfile(
    val id: String,
    val name: String,
    val apiBase: String,
)

class EnvironmentAllowList private constructor(val profiles: List<EnvironmentProfile>) {
    val default: EnvironmentProfile get() = profiles.first()

    fun require(id: String): EnvironmentProfile =
        profiles.singleOrNull { it.id == id } ?: throw IllegalArgumentException("unapproved environment")

    companion object {
        fun parse(omen: String, aws: String, allowDebug: Boolean): EnvironmentAllowList {
            val profiles = listOf(
                EnvironmentProfile("omen", "Omen hospital server", omen),
                EnvironmentProfile("aws", "AWS standby server", aws),
            )
            profiles.forEach { profile ->
                val uri = URI(profile.apiBase)
                val debugHost = uri.host in setOf("localhost", "127.0.0.1", "10.0.2.2")
                require(uri.scheme == "https" || allowDebug && uri.scheme == "http" && debugHost)
                require(uri.userInfo == null && uri.query == null && uri.fragment == null)
                if (!allowDebug) {
                    require(uri.host == "${profile.id}.opd.radpretation.ai")
                    require(uri.port == -1 && uri.path.trimEnd('/') == "/api")
                }
            }
            return EnvironmentAllowList(profiles)
        }

        fun fromBuildConfig(): EnvironmentAllowList =
            parse(BuildConfig.OMEN_API_BASE, BuildConfig.AWS_API_BASE, BuildConfig.ALLOW_DEBUG_ENDPOINTS)
    }
}

class EnvironmentStore(
    private val context: Context,
    val allowList: EnvironmentAllowList,
) {
    private object Keys {
        val SELECTED = stringPreferencesKey("selected_environment")
    }

    val selected: Flow<EnvironmentProfile> = context.environmentStore.data.map { preferences ->
        allowList.require(preferences[Keys.SELECTED] ?: allowList.default.id)
    }

    suspend fun current(): EnvironmentProfile = selected.first()

    suspend fun select(id: String) {
        allowList.require(id)
        context.environmentStore.edit { it[Keys.SELECTED] = id }
    }
}

@Serializable
data class EnvironmentIdentity(
    @SerialName("environment_id") val environmentId: String,
    @SerialName("human_name") val humanName: String,
    @SerialName("api_contract_version") val apiContractVersion: String,
    @SerialName("release_sha") val releaseSha: String,
    @SerialName("current_time") val currentTime: String,
)

data class EnvironmentProbe(
    val profile: EnvironmentProfile,
    val identity: EnvironmentIdentity,
    val clockSkewSeconds: Long,
)

class EnvironmentMismatch(message: String) : IllegalStateException(message)

class EnvironmentPairing(
    private val environments: EnvironmentStore,
    private val tokens: TokenStore,
    private val api: ApiClient,
    private val clearClientState: suspend () -> Unit,
) {
    companion object {
        const val API_CONTRACT = "2026-07-28"
        const val MAX_CLOCK_SKEW_SECONDS = 300L
    }

    suspend fun probe(profile: EnvironmentProfile): EnvironmentProbe {
        val probeClient = ApiClient(profile.apiBase, tokens)
        val health = probeClient.get("/health", authenticated = false)
        if (!health.body.contains("\"status\":\"ok\"") && !health.body.contains("\"status\": \"ok\"")) {
            throw EnvironmentMismatch("health response is not compatible")
        }
        val identityResponse = probeClient.get("/environment", authenticated = false)
        val identity = probeClient.json.decodeFromString(
            EnvironmentIdentity.serializer(),
            identityResponse.body,
        )
        if (identity.environmentId != profile.id) throw EnvironmentMismatch("server identity does not match")
        if (identity.apiContractVersion != API_CONTRACT) throw EnvironmentMismatch("API version does not match")
        val skew = kotlin.math.abs(Duration.between(Instant.now(), Instant.parse(identity.currentTime)).seconds)
        if (skew > MAX_CLOCK_SKEW_SECONDS) throw EnvironmentMismatch("server clock is too far away")
        return EnvironmentProbe(profile, identity, skew)
    }

    suspend fun confirmSwitch(probe: EnvironmentProbe) {
        val checked = probe(probe.profile)
        if (checked.identity.releaseSha != probe.identity.releaseSha) {
            throw EnvironmentMismatch("server changed after confirmation")
        }
        api.cancelInFlight()
        tokens.clear()
        clearClientState()
        environments.select(probe.profile.id)
    }
}
