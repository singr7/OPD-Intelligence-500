package ai.radpretation.opd.data

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** Phone-OTP sign-in (doc 03 §1c.7). Two calls, no password, no email. */
class AuthRepository(private val api: ApiClient, private val tokens: TokenStore) {

    suspend fun requestOtp(phone: String): OtpRequestOut {
        val body = buildJsonObject { put("phone", JsonPrimitive(phone)) }
        val response = api.post(
            "/auth/patient/otp/request",
            api.json.encodeToString(JsonObject.serializer(), body),
            authenticated = false,
        )
        return api.json.decodeFromString(OtpRequestOut.serializer(), response.body)
    }

    suspend fun verifyOtp(phone: String, code: String): TokenPair {
        val body = buildJsonObject {
            put("phone", JsonPrimitive(phone))
            put("code", JsonPrimitive(code))
        }
        val response = api.post(
            "/auth/patient/otp/verify",
            api.json.encodeToString(JsonObject.serializer(), body),
            authenticated = false,
        )
        val pair = api.json.decodeFromString(TokenPair.serializer(), response.body)
        tokens.save(pair)
        pair.profiles.firstOrNull { it.patientId == pair.patientId }?.let {
            tokens.rememberName(it.name)
        }
        return pair
    }

    /** Open one of the other files this phone is entitled to (a shared handset,
     *  a son who is caregiver to both parents). */
    suspend fun switchTo(patientId: String): TokenPair {
        val body = buildJsonObject { put("patient_id", JsonPrimitive(patientId)) }
        val response = api.post(
            "/auth/patient/switch",
            api.json.encodeToString(JsonObject.serializer(), body),
        )
        val pair = api.json.decodeFromString(TokenPair.serializer(), response.body)
        tokens.save(pair)
        return pair
    }

    suspend fun signOut() {
        val refresh = tokens.refreshToken()
        if (refresh != null) {
            val body = buildJsonObject { put("refresh_token", JsonPrimitive(refresh)) }
            // Best effort: the local tokens go either way. A patient handing her
            // phone back at a village camp should not have to be online to be
            // signed out of it.
            runCatching {
                api.post(
                    "/auth/logout",
                    api.json.encodeToString(JsonObject.serializer(), body),
                    authenticated = false,
                )
            }
        }
        tokens.clear()
    }
}
