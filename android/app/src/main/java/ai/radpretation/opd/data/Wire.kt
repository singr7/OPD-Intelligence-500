package ai.radpretation.opd.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * The `/patient` wire shapes, mirrored from `backend/app/routes/patient.py`.
 *
 * Every field is either non-null with a default or explicitly nullable, and
 * `Json { ignoreUnknownKeys = true }` does the rest: a phone in a village runs
 * whatever version was installed six months ago, and a field added by a later
 * session must never be the reason her prescriptions stop opening.
 */

@Serializable
data class OtpRequestOut(
    val sent: Boolean = false,
    @SerialName("expires_at") val expiresAt: String? = null,
    @SerialName("debug_code") val debugCode: String? = null,
)

@Serializable
data class ProfileOut(
    @SerialName("patient_id") val patientId: String,
    val name: String,
    val via: String,
    val relation: String? = null,
)

@Serializable
data class TokenPair(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("expires_at") val expiresAt: String? = null,
    @SerialName("patient_id") val patientId: String = "",
    val via: String = "self",
    val profiles: List<ProfileOut> = emptyList(),
)

@Serializable
data class MeOut(
    @SerialName("patient_id") val patientId: String,
    val name: String,
    val lang: String = "hi",
    val mrn: String = "",
    val village: String? = null,
    val via: String = "self",
    val hospital: String? = null,
)

@Serializable
data class ScheduleOut(
    val morning: Boolean = false,
    val afternoon: Boolean = false,
    val night: Boolean = false,
    @SerialName("per_day") val perDay: Int? = null,
    @SerialName("slots_known") val slotsKnown: Boolean = false,
    val source: String = "",
)

@Serializable
data class MedOut(
    val name: String,
    val dose: String? = null,
    val route: String? = null,
    val freq: String? = null,
    val duration: String? = null,
    val schedule: ScheduleOut? = null,
    val flagged: Boolean = false,
    @SerialName("flag_reason") val flagReason: String? = null,
)

@Serializable
data class FileEntryOut(
    val kind: String,
    val id: String,
    @SerialName("visit_id") val visitId: String,
    val at: String,
    val department: String = "",
    val doctor: String? = null,
    val meds: List<MedOut> = emptyList(),
    @SerialName("summary_md") val summaryMd: String? = null,
    @SerialName("chief_complaint") val chiefComplaint: String? = null,
    @SerialName("red_flags") val redFlags: List<Map<String, kotlinx.serialization.json.JsonElement>> = emptyList(),
)

@Serializable
data class CareFileOut(
    val patient: MeOut,
    val revision: String? = null,
    val entries: List<FileEntryOut> = emptyList(),
)

@Serializable
data class QueuePositionOut(
    @SerialName("in_queue") val inQueue: Boolean = false,
    @SerialName("visit_id") val visitId: String? = null,
    @SerialName("token_no") val tokenNo: Int? = null,
    val department: String? = null,
    val state: String? = null,
    val ahead: Int? = null,
    @SerialName("est_wait_low") val estWaitLow: Int? = null,
    @SerialName("est_wait_high") val estWaitHigh: Int? = null,
    @SerialName("leave_by") val leaveBy: String? = null,
    @SerialName("now_serving") val nowServing: Int? = null,
)

@Serializable
data class ArriveOut(
    @SerialName("token_no") val tokenNo: Int,
    val department: String = "",
    @SerialName("already_queued") val alreadyQueued: Boolean = false,
    val position: QueuePositionOut = QueuePositionOut(),
)

@Serializable
data class OptionOut(
    val id: String = "",
    val label: String = "",
    val icon: String? = null,
)

@Serializable
data class NodeOut(
    val id: String,
    val type: String,
    val text: String,
    val options: List<OptionOut> = emptyList(),
    val min: Double? = null,
    val max: Double? = null,
    val unit: String? = null,
    val audio: String? = null,
)

@Serializable
data class DeptOut(val key: String, val name: String)

@Serializable
data class IntakeStartOut(
    val status: String,
    @SerialName("session_id") val sessionId: String? = null,
    @SerialName("visit_id") val visitId: String? = null,
    val tier: String? = null,
    val department: DeptOut? = null,
    @SerialName("tree_key") val treeKey: String? = null,
    val node: NodeOut? = null,
    val complete: Boolean = false,
    val departments: List<DeptOut> = emptyList(),
    val reason: String? = null,
)

@Serializable
data class AnswerOut(
    val ok: Boolean,
    @SerialName("node_id") val nodeId: String,
    val complete: Boolean = false,
    val error: String? = null,
    val node: NodeOut? = null,
    val clarify: String? = null,
    @SerialName("adaptive_exhausted") val adaptiveExhausted: Boolean = false,
)

@Serializable
data class FinishOut(
    val readback: String,
    @SerialName("summary_md") val summaryMd: String? = null,
    val complete: Boolean = false,
)

@Serializable
data class IntakeConfirmOut(
    @SerialName("visit_id") val visitId: String? = null,
    val department: DeptOut? = null,
    @SerialName("token_no") val tokenNo: Int? = null,
    val message: String = "",
)

@Serializable
data class DoseOut(
    @SerialName("med_index") val medIndex: Int,
    val drug: String,
    val dose: String? = null,
    val route: String? = null,
    val duration: String? = null,
    val slot: String,
    val at: String? = null,
)

@Serializable
data class ReminderPlanOut(
    @SerialName("prescription_id") val prescriptionId: String? = null,
    @SerialName("prescribed_on") val prescribedOn: String? = null,
    val doses: List<DoseOut> = emptyList(),
    val unscheduled: List<String> = emptyList(),
)

@Serializable
data class DoseEventOut(
    val recorded: Boolean = false,
    @SerialName("caregiver_notified") val caregiverNotified: Boolean = false,
)

@Serializable
data class CycleOut(
    @SerialName("appointment_id") val appointmentId: String? = null,
    val at: String,
    @SerialName("cycle_no") val cycleNo: Int = 1,
    val doctor: String? = null,
    val department: String = "",
    val status: String = "",
    val title: String = "",
    val expect: List<String> = emptyList(),
)

@Serializable
data class CaregiverOut(
    val id: String,
    val phone: String,
    val name: String? = null,
    val relation: String? = null,
    val status: String,
    @SerialName("consented_at") val consentedAt: String? = null,
)

@Serializable
data class AppointmentOut(
    val id: String,
    @SerialName("slot_at") val slotAt: String,
    val status: String,
    @SerialName("slot_type") val slotType: String? = null,
    @SerialName("doctor_name") val doctorName: String? = null,
    @SerialName("department_name") val departmentName: String = "",
)
