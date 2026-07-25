package ai.radpretation.opd.util

import ai.radpretation.opd.data.FileEntryOut
import ai.radpretation.opd.data.MeOut
import android.content.Context
import android.graphics.Paint
import android.graphics.Typeface
import android.graphics.pdf.PdfDocument
import androidx.core.content.FileProvider
import android.net.Uri
import java.io.File

/**
 * "Shareable to any doctor as PDF" (doc 03 §1c.1).
 *
 * Built with the platform's own `PdfDocument` — no library, no HTML renderer,
 * no bytes added to the APK. The page is deliberately plain: this is a document
 * a doctor in another district will read on a cracked screen or print in black
 * and white, so it is text, in order, with the drug lines set apart.
 *
 * Two rules it inherits from the printed prescription (S11):
 *
 * * A drug is written **exactly as the doctor said it** — the name on the
 *   snapshot, never a formulary generic swapped in.
 * * A line the hospital flagged says so on the page. A prescription that hides
 *   "not on our formulary" from the next doctor is worse than no prescription.
 */
object CareFilePdf {

    private const val PAGE_WIDTH = 595 // A4 at 72dpi
    private const val PAGE_HEIGHT = 842
    private const val MARGIN = 40f

    fun write(
        context: Context,
        patient: MeOut?,
        entries: List<FileEntryOut>,
        hospital: String?,
    ): Uri {
        val document = PdfDocument()
        val title = Paint().apply { textSize = 18f; typeface = Typeface.DEFAULT_BOLD }
        val heading = Paint().apply { textSize = 13f; typeface = Typeface.DEFAULT_BOLD }
        val body = Paint().apply { textSize = 11f }
        val muted = Paint().apply { textSize = 10f; color = 0xFF5C6E69.toInt() }

        var pageNumber = 1
        var page = document.startPage(PdfDocument.PageInfo.Builder(PAGE_WIDTH, PAGE_HEIGHT, pageNumber).create())
        var canvas = page.canvas
        var y = MARGIN + 20f

        fun newPage() {
            document.finishPage(page)
            pageNumber++
            page = document.startPage(
                PdfDocument.PageInfo.Builder(PAGE_WIDTH, PAGE_HEIGHT, pageNumber).create(),
            )
            canvas = page.canvas
            y = MARGIN + 20f
        }

        fun line(text: String, paint: Paint, gap: Float = 16f) {
            if (y > PAGE_HEIGHT - MARGIN) newPage()
            // Wrap by measuring: no StaticLayout, because this is plain prose in
            // four possible scripts and the platform's measurer handles them all.
            var remaining = text
            val maxWidth = PAGE_WIDTH - 2 * MARGIN
            while (remaining.isNotEmpty()) {
                val count = paint.breakText(remaining, true, maxWidth, null)
                val cut = if (count >= remaining.length) remaining.length else {
                    remaining.lastIndexOf(' ', count).takeIf { it > 0 } ?: count
                }
                canvas.drawText(remaining.substring(0, cut).trim(), MARGIN, y, paint)
                y += gap
                if (y > PAGE_HEIGHT - MARGIN) newPage()
                remaining = remaining.substring(cut).trim()
            }
        }

        line(patient?.name ?: "My Cancer Care File", title, 24f)
        patient?.let { line("MRN ${it.mrn}${it.village?.let { v -> " · $v" } ?: ""}", muted, 18f) }
        hospital?.let { line(it, muted, 22f) }

        for (entry in entries) {
            y += 8f
            val kind = if (entry.kind == "prescription") "Prescription" else "Visit summary"
            line("$kind — ${entry.at.take(10)} · ${entry.department}", heading, 18f)
            entry.doctor?.let { line("Dr. $it", muted, 16f) }

            entry.chiefComplaint?.let { line(it, body) }
            entry.summaryMd?.let { line(it, body) }

            for (med in entry.meds) {
                val parts = listOfNotNull(
                    med.name,
                    med.dose,
                    med.freq,
                    med.duration,
                ).joinToString("  ·  ")
                line("•  $parts", body)
                med.flagReason?.takeIf { med.flagged }?.let { line("     ⚠ $it", muted, 14f) }
            }
            y += 6f
        }

        document.finishPage(page)

        val dir = File(context.cacheDir, "shared").apply { mkdirs() }
        val file = File(dir, "care-file.pdf")
        file.outputStream().use { document.writeTo(it) }
        document.close()

        return FileProvider.getUriForFile(context, "${context.packageName}.files", file)
    }
}
