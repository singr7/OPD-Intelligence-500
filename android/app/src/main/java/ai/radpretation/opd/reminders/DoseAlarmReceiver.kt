package ai.radpretation.opd.reminders

import ai.radpretation.opd.MainActivity
import ai.radpretation.opd.OpdApp
import ai.radpretation.opd.R
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager

/**
 * A dose is due.
 *
 * The notification carries the two actions the patient will actually use, so
 * "taken" costs one tap from the lock screen and never requires opening the app
 * — which matters at 8am in a house where the phone belongs to the son.
 *
 * Neither action talks to the network here. Both hand the report to
 * [DoseReportWorker], because a broadcast receiver gets about ten seconds and a
 * 2G round trip does not fit in them.
 */
class DoseAlarmReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val prescriptionId = intent.getStringExtra(DoseScheduler.EXTRA_PRESCRIPTION) ?: return
        val medIndex = intent.getIntExtra(DoseScheduler.EXTRA_MED_INDEX, -1)
        val scheduledFor = intent.getStringExtra(DoseScheduler.EXTRA_SCHEDULED_FOR) ?: return
        val drug = intent.getStringExtra(DoseScheduler.EXTRA_DRUG).orEmpty()
        val dose = intent.getStringExtra(DoseScheduler.EXTRA_DOSE).orEmpty()

        when (intent.action) {
            ACTION_TAKEN -> {
                report(context, prescriptionId, medIndex, scheduledFor, "taken")
                cancel(context, scheduledFor)
            }
            ACTION_MISSED -> {
                report(context, prescriptionId, medIndex, scheduledFor, "missed")
                cancel(context, scheduledFor)
            }
            else -> notify(context, prescriptionId, medIndex, scheduledFor, drug, dose)
        }
    }

    private fun report(
        context: Context,
        prescriptionId: String,
        medIndex: Int,
        scheduledFor: String,
        status: String,
    ) {
        WorkManager.getInstance(context).enqueueUniqueWork(
            "dose-$scheduledFor-$medIndex",
            ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<DoseReportWorker>()
                .setInputData(
                    Data.Builder()
                        .putString(DoseReportWorker.KEY_PRESCRIPTION, prescriptionId)
                        .putInt(DoseReportWorker.KEY_MED_INDEX, medIndex)
                        .putString(DoseReportWorker.KEY_SCHEDULED_FOR, scheduledFor)
                        .putString(DoseReportWorker.KEY_STATUS, status)
                        .build(),
                )
                .build(),
        )
    }

    private fun notify(
        context: Context,
        prescriptionId: String,
        medIndex: Int,
        scheduledFor: String,
        drug: String,
        dose: String,
    ) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL,
                context.getString(R.string.reminders_channel),
                NotificationManager.IMPORTANCE_HIGH,
            ),
        )

        fun action(label: String, act: String): Notification.Action {
            val intent = Intent(context, DoseAlarmReceiver::class.java).apply {
                action = act
                putExtra(DoseScheduler.EXTRA_PRESCRIPTION, prescriptionId)
                putExtra(DoseScheduler.EXTRA_MED_INDEX, medIndex)
                putExtra(DoseScheduler.EXTRA_SCHEDULED_FOR, scheduledFor)
            }
            val pending = PendingIntent.getBroadcast(
                context,
                (act + scheduledFor + medIndex).hashCode(),
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            return Notification.Action.Builder(null, label, pending).build()
        }

        val open = PendingIntent.getActivity(
            context,
            scheduledFor.hashCode(),
            Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val notification = Notification.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(context.getString(R.string.reminders_notification_title))
            .setContentText(context.getString(R.string.reminders_notification_body, drug, dose))
            .setContentIntent(open)
            .setAutoCancel(true)
            .addAction(action(context.getString(R.string.reminders_taken), ACTION_TAKEN))
            .addAction(action(context.getString(R.string.reminders_missed), ACTION_MISSED))
            .build()

        manager.notify(scheduledFor.hashCode() + medIndex, notification)

        // Arm the next window while we are awake. `setExactAndAllowWhileIdle`
        // schedules one alarm at a time, so each firing is also the trigger to
        // top the horizon back up.
        runCatching {
            val container = OpdApp.containerOf(context)
            kotlinx.coroutines.runBlocking {
                container.patients.cachedReminderPlan()?.let { DoseScheduler.schedule(context, it) }
            }
        }
    }

    private fun cancel(context: Context, scheduledFor: String) {
        context.getSystemService(NotificationManager::class.java)?.cancelAll()
    }

    companion object {
        const val CHANNEL = "doses"
        const val ACTION_TAKEN = "ai.radpretation.opd.DOSE_TAKEN"
        const val ACTION_MISSED = "ai.radpretation.opd.DOSE_MISSED"
    }
}
