package ai.radpretation.opd.ui.screens

import ai.radpretation.opd.AppContainer
import ai.radpretation.opd.R
import ai.radpretation.opd.data.FileEntryOut
import ai.radpretation.opd.ui.Muted
import ai.radpretation.opd.ui.OfflineBanner
import ai.radpretation.opd.ui.QuietButton
import ai.radpretation.opd.ui.SectionCard
import ai.radpretation.opd.ui.WarningStamp
import ai.radpretation.opd.ui.theme.Ink
import ai.radpretation.opd.ui.theme.PrimarySoft
import ai.radpretation.opd.util.CareFilePdf
import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp

/**
 * My Cancer Care File — the reason a patient installs this app (doc 03 §1c.1).
 *
 * It renders **from the cache, always**. The network refresh runs on entry and
 * its failure is not an error state: a patient standing in a field with no bars
 * must see the same list she saw at home. That is the whole feature; a spinner
 * here would be a bug.
 */
@Composable
fun FileScreen(container: AppContainer) {
    val entries by container.patients.cachedFile.collectAsState(initial = emptyList())
    val patient by container.patients.cachedPatient.collectAsState(initial = null)
    var offline by remember { mutableStateOf(false) }
    val context = LocalContext.current

    LaunchedEffect(Unit) {
        offline = runCatching { container.patients.refreshFile() }.isFailure
    }

    Column(Modifier.fillMaxSize()) {
        if (offline) OfflineBanner(text = stringResource(R.string.file_offline))

        LazyColumn(
            Modifier.fillMaxSize().testTag("file_list"),
            contentPadding = PaddingValues(20.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            item {
                Text(
                    stringResource(R.string.file_title),
                    style = MaterialTheme.typography.headlineMedium,
                )
            }

            if (entries.isEmpty()) {
                item {
                    Spacer(Modifier.height(20.dp))
                    Muted(stringResource(R.string.file_empty))
                }
            }

            items(entries, key = { "${it.kind}:${it.id}" }) { entry ->
                FileEntryCard(entry)
            }

            if (entries.isNotEmpty()) {
                item {
                    Spacer(Modifier.height(8.dp))
                    QuietButton(
                        text = stringResource(R.string.file_share),
                        modifier = Modifier.testTag("file_share"),
                        onClick = {
                            val uri = CareFilePdf.write(
                                context,
                                patient,
                                entries,
                                patient?.hospital,
                            )
                            val share = Intent(Intent.ACTION_SEND).apply {
                                type = "application/pdf"
                                putExtra(Intent.EXTRA_STREAM, uri)
                                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                            }
                            context.startActivity(
                                Intent.createChooser(
                                    share,
                                    context.getString(R.string.file_share_title),
                                ),
                            )
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun FileEntryCard(entry: FileEntryOut) {
    SectionCard {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(
                stringResource(
                    if (entry.kind == "prescription") R.string.file_prescription
                    else R.string.file_summary,
                ),
                style = MaterialTheme.typography.titleMedium,
            )
            Muted(entry.at.take(10))
        }
        Spacer(Modifier.height(4.dp))
        Muted(listOfNotNull(entry.department.ifBlank { null }, entry.doctor).joinToString(" · "))

        entry.chiefComplaint?.takeIf { it.isNotBlank() }?.let {
            Spacer(Modifier.height(12.dp))
            Text(it, style = MaterialTheme.typography.bodyLarge)
        }

        entry.summaryMd?.takeIf { it.isNotBlank() }?.let {
            Spacer(Modifier.height(10.dp))
            Text(it, style = MaterialTheme.typography.bodyMedium, color = Ink)
        }

        entry.meds.forEach { med ->
            Spacer(Modifier.height(12.dp))
            Column(
                Modifier
                    .fillMaxWidth()
                    .background(PrimarySoft, RoundedCornerShape(14.dp))
                    .padding(14.dp),
            ) {
                Text(med.name, style = MaterialTheme.typography.titleMedium)
                val detail = listOfNotNull(med.dose, med.freq, med.duration).joinToString(" · ")
                if (detail.isNotBlank()) {
                    Spacer(Modifier.height(4.dp))
                    Muted(detail)
                }
            }
            if (med.flagged) {
                Spacer(Modifier.height(8.dp))
                WarningStamp(med.flagReason ?: stringResource(R.string.file_flagged))
            }
        }
    }
}
