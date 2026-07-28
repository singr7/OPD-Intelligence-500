package ai.radpretation.opd.intake

import ai.radpretation.opd.data.local.PendingIntakeDao
import ai.radpretation.opd.data.local.PendingIntakeRow

class CrossEnvironmentSync : IllegalStateException("offline intake belongs to another environment")

/**
 * Ownership travels with unsynced PII. Switching servers never rewrites it and
 * a sync can select only rows created for the currently paired environment.
 */
class OwnedOfflineIntakes(
    private val dao: PendingIntakeDao,
    private val environmentId: suspend () -> String,
) {
    suspend fun save(id: String, payload: String, createdAt: Long = System.currentTimeMillis()) {
        dao.put(PendingIntakeRow(id, environmentId(), payload, createdAt))
    }

    suspend fun pendingForCurrent(): List<PendingIntakeRow> = dao.ownedBy(environmentId())

    suspend fun requireCurrent(id: String): PendingIntakeRow {
        val row = dao.find(id) ?: throw NoSuchElementException(id)
        if (row.environmentId != environmentId()) throw CrossEnvironmentSync()
        return row
    }

    suspend fun done(id: String) {
        requireCurrent(id)
        dao.done(id)
    }
}
