package ai.radpretation.opd.data.local

import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.RoomDatabase
import androidx.room.Transaction
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import kotlinx.coroutines.flow.Flow

/**
 * The phone's own copy of the care file (doc 03 §1c.1: "works offline").
 *
 * The rows are stored as the **server's own JSON**, not as a re-modelled schema.
 * That is deliberate: the file is read-only on the phone, so a second model
 * would buy nothing and cost a migration every time the API grows a field. What
 * the columns do carry is enough to sort, filter and search without parsing:
 * the kind, the date, and the department.
 *
 * The unsent dose queue is the one table that is genuinely local state — it
 * exists because a reminder fires whether or not there is a signal, and "I took
 * it" must survive until the phone can say so.
 */

@Entity(tableName = "file_entries")
data class FileEntryRow(
    @PrimaryKey val id: String,
    val kind: String,
    @ColumnInfo(name = "at_iso") val atIso: String,
    val department: String,
    val doctor: String?,
    /** The `FileEntryOut` as it arrived, verbatim. */
    val payload: String,
)

@Entity(tableName = "kv")
data class KvRow(@PrimaryKey val key: String, val value: String)

@Entity(tableName = "pending_doses")
data class PendingDoseRow(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    @ColumnInfo(name = "prescription_id") val prescriptionId: String,
    @ColumnInfo(name = "med_index") val medIndex: Int,
    /** The dose's own time, ISO-8601 with an offset — the server's natural key. */
    @ColumnInfo(name = "scheduled_for") val scheduledFor: String,
    val status: String,
    @ColumnInfo(name = "reported_at") val reportedAt: Long,
    @ColumnInfo(name = "environment_id") val environmentId: String,
)

@Entity(tableName = "pending_intakes")
data class PendingIntakeRow(
    @PrimaryKey val id: String,
    @ColumnInfo(name = "environment_id") val environmentId: String,
    /** Patient answers encrypted by Android's app sandbox; never logged. */
    val payload: String,
    @ColumnInfo(name = "created_at") val createdAt: Long,
)

@Dao
interface FileDao {
    @Query("SELECT * FROM file_entries ORDER BY at_iso DESC")
    fun entries(): Flow<List<FileEntryRow>>

    @Query("SELECT * FROM file_entries ORDER BY at_iso DESC")
    suspend fun entriesNow(): List<FileEntryRow>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(rows: List<FileEntryRow>)

    @Query("DELETE FROM file_entries")
    suspend fun clear()

    /**
     * Replace the whole file in one transaction.
     *
     * Clearing and re-inserting separately would leave a window where a patient
     * who opened the app at the wrong instant saw an empty file — the most
     * alarming possible bug in an app whose promise is "your papers are here".
     */
    @Transaction
    suspend fun replaceAll(rows: List<FileEntryRow>) {
        clear()
        upsert(rows)
    }
}

@Dao
interface KvDao {
    @Query("SELECT value FROM kv WHERE key = :key")
    suspend fun get(key: String): String?

    @Query("SELECT value FROM kv WHERE key = :key")
    fun watch(key: String): Flow<String?>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun put(row: KvRow)

    @Query("DELETE FROM kv")
    suspend fun clear()
}

@Dao
interface DoseDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun enqueue(row: PendingDoseRow): Long

    @Query("SELECT * FROM pending_doses WHERE environment_id = :environmentId ORDER BY reported_at")
    suspend fun pending(environmentId: String): List<PendingDoseRow>

    @Query("DELETE FROM pending_doses WHERE id = :id")
    suspend fun done(id: Long)

    @Query("SELECT COUNT(*) FROM pending_doses")
    suspend fun count(): Int
}

@Dao
interface PendingIntakeDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun put(row: PendingIntakeRow)

    @Query("SELECT * FROM pending_intakes WHERE environment_id = :environmentId ORDER BY created_at")
    suspend fun ownedBy(environmentId: String): List<PendingIntakeRow>

    @Query("SELECT * FROM pending_intakes WHERE id = :id")
    suspend fun find(id: String): PendingIntakeRow?

    @Query("DELETE FROM pending_intakes WHERE id = :id")
    suspend fun done(id: String)
}

@Database(
    entities = [FileEntryRow::class, KvRow::class, PendingDoseRow::class, PendingIntakeRow::class],
    version = 2,
    exportSchema = true,
)
abstract class OpdDatabase : RoomDatabase() {
    abstract fun files(): FileDao
    abstract fun kv(): KvDao
    abstract fun doses(): DoseDao
    abstract fun pendingIntakes(): PendingIntakeDao

    companion object {
        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                // The single S16 server was the Omen. Existing unsent dose
                // reports retain that ownership during the first upgrade.
                db.execSQL(
                    "ALTER TABLE pending_doses ADD COLUMN environment_id TEXT NOT NULL DEFAULT 'omen'",
                )
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS pending_intakes (
                        id TEXT NOT NULL PRIMARY KEY,
                        environment_id TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        created_at INTEGER NOT NULL
                    )
                    """.trimIndent(),
                )
            }
        }

        const val KEY_FILE_ETAG = "file_etag"
        const val KEY_FILE_PATIENT = "file_patient"
        const val KEY_REMINDER_PLAN = "reminder_plan"
        const val KEY_CHEMO_CALENDAR = "chemo_calendar"
        const val KEY_QUEUE_LAST = "queue_last"
        const val KEY_TRAVEL_MINUTES = "travel_minutes"
    }
}
