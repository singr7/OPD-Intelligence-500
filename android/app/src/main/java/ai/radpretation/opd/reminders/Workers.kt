package ai.radpretation.opd.reminders

import ai.radpretation.opd.OpdApp
import ai.radpretation.opd.data.OfflineException
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.runBlocking
import java.time.LocalDateTime
import java.util.concurrent.TimeUnit

/**
 * One dose report, delivered eventually.
 *
 * The repository queues the row first and tries the network second, so this
 * worker's real job is the retry: a phone in a village will fail this many times
 * before it succeeds, and every failure must be a `retry()` rather than a lost
 * "I took it".
 */
class DoseReportWorker(context: Context, params: WorkerParameters) :
    CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val prescriptionId = inputData.getString(KEY_PRESCRIPTION) ?: return Result.success()
        val medIndex = inputData.getInt(KEY_MED_INDEX, -1)
        val scheduledFor = inputData.getString(KEY_SCHEDULED_FOR) ?: return Result.success()
        val status = inputData.getString(KEY_STATUS) ?: return Result.success()

        val repo = OpdApp.containerOf(applicationContext).patients
        return try {
            repo.reportDose(prescriptionId, medIndex, scheduledFor, status)
            // `reportDose` queues then attempts; anything still queued is drained
            // here so a report made while offline this morning goes out with the
            // first one made on wifi tonight.
            repo.drainDoses()
            Result.success()
        } catch (_: OfflineException) {
            Result.retry()
        } catch (_: Exception) {
            Result.retry()
        }
    }

    companion object {
        const val KEY_PRESCRIPTION = "prescription_id"
        const val KEY_MED_INDEX = "med_index"
        const val KEY_SCHEDULED_FOR = "scheduled_for"
        const val KEY_STATUS = "status"
    }
}

/**
 * The daily catch-up: refresh the file, refresh the plan, re-arm the alarms,
 * push anything the phone has been holding.
 *
 * Deliberately one worker rather than four: they share a network window, and on
 * a metered connection four wake-ups cost four radio spin-ups.
 */
class SyncWorker(context: Context, params: WorkerParameters) :
    CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val container = OpdApp.containerOf(applicationContext)
        if (container.tokens.refreshToken() == null) return Result.success()

        return try {
            container.patients.drainDoses()
            container.patients.refreshFile()
            val plan = container.patients.refreshReminderPlan()
            DoseScheduler.schedule(applicationContext, plan, LocalDateTime.now())
            container.patients.refreshChemoCalendar()
            Result.success()
        } catch (_: OfflineException) {
            Result.retry()
        } catch (_: Exception) {
            // An auth failure clears the session; nothing to retry until the
            // patient signs in again, and a retry storm on a dead token is how
            // a phone's battery disappears.
            Result.success()
        }
    }

    companion object {
        private const val NAME = "opd-sync"

        fun enqueue(context: Context) {
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                PeriodicWorkRequestBuilder<SyncWorker>(6, TimeUnit.HOURS)
                    .setConstraints(
                        Constraints.Builder()
                            .setRequiredNetworkType(NetworkType.CONNECTED)
                            .build(),
                    )
                    .build(),
            )
        }
    }
}

/**
 * Alarms do not survive a reboot, and a phone in a village is rebooted often
 * (flat battery, then charged at a neighbour's). Re-arming on boot is the
 * difference between a reminder app and a reminder app that works for a week.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val container = OpdApp.containerOf(context)
        runBlocking {
            container.patients.cachedReminderPlan()?.let {
                DoseScheduler.schedule(context, it, LocalDateTime.now())
            }
        }
        SyncWorker.enqueue(context)
    }
}
