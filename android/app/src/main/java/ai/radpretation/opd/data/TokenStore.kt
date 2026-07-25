package ai.radpretation.opd.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.runBlocking

private val Context.authStore by preferencesDataStore(name = "opd_auth")

/**
 * Where the session lives between launches.
 *
 * Plain DataStore, not EncryptedSharedPreferences: the tokens are already
 * scoped to one patient's own file, app-private storage is not readable by
 * other apps, and the keystore-backed alternative costs ~600KB and a class of
 * "keystore invalidated after a fingerprint change" bugs that would lock a
 * patient out of her prescriptions in a waiting room. Registered in STATE.md's
 * stubs so the choice is visible rather than accidental.
 */
class TokenStore(private val context: Context) {

    private object Keys {
        val ACCESS = stringPreferencesKey("access")
        val REFRESH = stringPreferencesKey("refresh")
        val PATIENT_ID = stringPreferencesKey("patient_id")
        val VIA = stringPreferencesKey("via")
        val NAME = stringPreferencesKey("name")
    }

    val signedIn: Flow<Boolean> = context.authStore.data.map { it[Keys.REFRESH] != null }

    val patientId: Flow<String?> = context.authStore.data.map { it[Keys.PATIENT_ID] }

    val via: Flow<String> = context.authStore.data.map { it[Keys.VIA] ?: "self" }

    val name: Flow<String?> = context.authStore.data.map { it[Keys.NAME] }

    suspend fun save(pair: TokenPair) {
        context.authStore.edit {
            it[Keys.ACCESS] = pair.accessToken
            it[Keys.REFRESH] = pair.refreshToken
            if (pair.patientId.isNotEmpty()) it[Keys.PATIENT_ID] = pair.patientId
            it[Keys.VIA] = pair.via
        }
    }

    suspend fun rememberName(name: String) {
        context.authStore.edit { it[Keys.NAME] = name }
    }

    suspend fun clear() {
        context.authStore.edit { it.clear() }
    }

    suspend fun accessToken(): String? = context.authStore.data.first()[Keys.ACCESS]

    suspend fun refreshToken(): String? = context.authStore.data.first()[Keys.REFRESH]

    /** For the WorkManager workers, which run outside a coroutine scope of their own. */
    fun blockingPatientId(): String? = runBlocking { context.authStore.data.first()[Keys.PATIENT_ID] }
}
