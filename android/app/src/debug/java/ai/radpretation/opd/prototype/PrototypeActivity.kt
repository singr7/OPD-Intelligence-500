package ai.radpretation.opd.prototype

import android.annotation.SuppressLint
import android.graphics.Color
import android.os.Bundle
import android.view.ViewGroup
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.core.view.WindowCompat

/**
 * Debug-only shell for the "Good Days" design prototype.
 *
 * This lives in src/debug so it is compiled out of release builds entirely —
 * the prototype is seeded fiction, and an APK that could show it to a patient
 * is a patient-safety problem, not a convenience. It also keeps the assets out
 * of the 15MB release budget the size gate enforces.
 *
 * The prototype is plain HTML/CSS/JS loaded from assets, so it runs offline and
 * needs no server. If it tests well the surfaces get rebuilt in Compose; this
 * exists to answer "does it land?" without paying for that first.
 */
class PrototypeActivity : ComponentActivity() {

    private lateinit var web: WebView

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, true)
        window.statusBarColor = PROTOTYPE_BACKGROUND
        window.navigationBarColor = PROTOTYPE_BACKGROUND

        web = WebView(this).apply {
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            )
            setBackgroundColor(PROTOTYPE_BACKGROUND)
            settings.javaScriptEnabled = true
            // No network, no content providers, no file access beyond the bundled
            // asset directory: the prototype is entirely self-contained.
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.domStorageEnabled = true
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(
                    view: WebView,
                    request: WebResourceRequest,
                ): Boolean = !request.url.toString().startsWith(ASSET_ROOT)
            }
            loadUrl(START_PAGE)
        }
        setContentView(web)

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack()) web.goBack() else finish()
            }
        })
    }

    override fun onDestroy() {
        web.destroy()
        super.onDestroy()
    }

    private companion object {
        const val ASSET_ROOT = "file:///android_asset/carecompass/"
        const val START_PAGE = ASSET_ROOT + "index-v3.html"
        val PROTOTYPE_BACKGROUND = Color.parseColor("#071614")
    }
}
