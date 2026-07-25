package ai.radpretation.opd.intake

import ai.radpretation.opd.data.AnswerOut
import ai.radpretation.opd.data.ApiClient
import ai.radpretation.opd.data.FinishOut
import ai.radpretation.opd.data.IntakeConfirmOut
import ai.radpretation.opd.data.IntakeStartOut
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * Talk-to-Dhara from home (doc 03 §1c.2), over the same four-tool contract every
 * other channel walks.
 *
 * Note what is *not* here: no tree, no branching, no red-flag rules. The phone
 * asks the server what to show next and sends back what was said or tapped. The
 * kiosk earned its offline walker with a golden-trace conformance suite (S7);
 * a second port in Kotlin would be a third implementation of the same clinical
 * logic to keep in step, so the app deliberately requires a signal to *do* an
 * intake — which is fine, because an intake done at home is done the evening
 * before, indoors, on the household's wifi or one bar of 4G. Reading the file
 * is what has to work in a field.
 */
class IntakeRepository(private val api: ApiClient) {

    suspend fun start(lang: String, chiefComplaint: String, deptKey: String?): IntakeStartOut {
        val body = buildJsonObject {
            put("lang", JsonPrimitive(lang))
            put("chief_complaint", JsonPrimitive(chiefComplaint))
            deptKey?.let { put("dept_key", JsonPrimitive(it)) }
        }
        val response = api.post(
            "/patient/intake/start",
            api.json.encodeToString(JsonObject.serializer(), body),
        )
        return api.json.decodeFromString(IntakeStartOut.serializer(), response.body)
    }

    /**
     * One answer. `value` is a tap; `rawText` is what she said.
     *
     * Both may be sent: a spoken answer with no `value` is what triggers the
     * server-side interpreter (doc 11 §2), and a tap after a failed listen sends
     * the value alone — the zero-AI path.
     */
    suspend fun answer(
        sessionId: String,
        nodeId: String,
        value: JsonElement?,
        rawText: String?,
        attempt: Int = 0,
    ): AnswerOut {
        val body = buildJsonObject {
            put("node_id", JsonPrimitive(nodeId))
            value?.let { put("value", it) }
            rawText?.let { put("raw_text", JsonPrimitive(it)) }
            put("attempt", JsonPrimitive(attempt))
        }
        val response = api.post(
            "/patient/intake/$sessionId/answer",
            api.json.encodeToString(JsonObject.serializer(), body),
        )
        return api.json.decodeFromString(AnswerOut.serializer(), response.body)
    }

    suspend fun finish(sessionId: String): FinishOut {
        val response = api.post("/patient/intake/$sessionId/finish")
        return api.json.decodeFromString(FinishOut.serializer(), response.body)
    }

    suspend fun confirm(sessionId: String): IntakeConfirmOut {
        val response = api.post("/patient/intake/$sessionId/confirm")
        return api.json.decodeFromString(IntakeConfirmOut.serializer(), response.body)
    }
}
