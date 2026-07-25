package ai.radpretation.opd.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

/** A request that failed in a way the UI has to say something about. */
class ApiException(val code: Int, val detail: String) : IOException("HTTP $code: $detail")

/** No network, no DNS, no route. Distinct from [ApiException] because the app's
 *  answer is different: fall back to what is on the phone, quietly. */
class OfflineException(cause: Throwable) : IOException("offline", cause)

/**
 * The one HTTP client. Small on purpose (doc 03 §1c.7's 15MB), and opinionated
 * about three things:
 *
 * 1. **Timeouts are short.** A patient on 2G in Ramgarh would rather see her
 *    cached file in eight seconds than a spinner for sixty.
 * 2. **401 rotates once, then logs out.** `refresh` is serialised behind a
 *    mutex, so ten screens waking at once produce one rotation and not ten —
 *    which matters because the server rotates refresh tokens single-use, and a
 *    stampede would revoke the winner's token as fast as it minted it.
 * 3. **It never throws on an empty file.** A 304 is an answer, not an error.
 */
class ApiClient(
    private val baseUrl: String,
    private val tokens: TokenStore,
    private val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .build(),
) {
    val json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
        encodeDefaults = true
    }

    private val refreshLock = Mutex()

    companion object {
        private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()
        const val NOT_MODIFIED = 304
    }

    data class Response(val code: Int, val body: String, val etag: String?)

    suspend fun get(path: String, etag: String? = null, authenticated: Boolean = true): Response =
        send("GET", path, null, etag, authenticated)

    suspend fun post(path: String, body: String? = null, authenticated: Boolean = true): Response =
        send("POST", path, body ?: "{}", null, authenticated)

    suspend fun delete(path: String, authenticated: Boolean = true): Response =
        send("DELETE", path, null, null, authenticated)

    private suspend fun send(
        method: String,
        path: String,
        body: String?,
        etag: String?,
        authenticated: Boolean,
        isRetry: Boolean = false,
    ): Response = withContext(Dispatchers.IO) {
        val builder = Request.Builder().url(baseUrl.trimEnd('/') + path)
        when (method) {
            "GET" -> builder.get()
            "DELETE" -> builder.delete()
            else -> builder.method(method, (body ?: "{}").toRequestBody(JSON_MEDIA))
        }
        etag?.let { builder.header("If-None-Match", it) }
        if (authenticated) {
            val access = tokens.accessToken() ?: throw ApiException(401, "not signed in")
            builder.header("Authorization", "Bearer $access")
        }

        val response = try {
            http.newCall(builder.build()).execute()
        } catch (e: IOException) {
            throw OfflineException(e)
        }

        response.use {
            val text = it.body?.string().orEmpty()
            if (it.code == 401 && authenticated && !isRetry) {
                return@withContext if (rotate()) {
                    send(method, path, body, etag, authenticated, isRetry = true)
                } else {
                    throw ApiException(401, "signed out")
                }
            }
            if (!it.isSuccessful && it.code != NOT_MODIFIED) {
                throw ApiException(it.code, detailOf(text))
            }
            Response(it.code, text, it.header("ETag"))
        }
    }

    /** True if the session is alive again. False means the patient must sign in. */
    private suspend fun rotate(): Boolean = refreshLock.withLock {
        val refresh = tokens.refreshToken() ?: return@withLock false
        // Another caller may have rotated while this one waited for the lock;
        // its new token is already stored, so retrying the original request is
        // the right move without spending this (now dead) refresh token.
        if (refresh != tokens.refreshToken()) return@withLock true

        val body = json.encodeToString(
            kotlinx.serialization.json.JsonObject.serializer(),
            kotlinx.serialization.json.JsonObject(
                mapOf("refresh_token" to kotlinx.serialization.json.JsonPrimitive(refresh)),
            ),
        )
        val request = Request.Builder()
            .url(baseUrl.trimEnd('/') + "/auth/refresh")
            .post(body.toRequestBody(JSON_MEDIA))
            .build()
        val response = try {
            withContext(Dispatchers.IO) { http.newCall(request).execute() }
        } catch (_: IOException) {
            // Offline is not signed-out. Keep the tokens; the next attempt with
            // a signal will rotate. Losing a session because a bus went through
            // a tunnel is exactly the failure this app cannot afford.
            return@withLock false
        }
        response.use {
            if (!it.isSuccessful) {
                tokens.clear()
                return@withLock false
            }
            val pair = json.decodeFromString(TokenPair.serializer(), it.body?.string().orEmpty())
            tokens.save(pair)
            true
        }
    }

    private fun detailOf(body: String): String = runCatching {
        json.parseToJsonElement(body)
            .let { it as? kotlinx.serialization.json.JsonObject }
            ?.get("detail")
            ?.let { d -> (d as? kotlinx.serialization.json.JsonPrimitive)?.content }
    }.getOrNull() ?: body.take(200)
}
