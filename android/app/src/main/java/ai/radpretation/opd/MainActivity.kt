package ai.radpretation.opd

import ai.radpretation.opd.reminders.SyncWorker
import ai.radpretation.opd.ui.screens.AppointmentsScreen
import ai.radpretation.opd.ui.screens.CalendarScreen
import ai.radpretation.opd.ui.screens.FamilyScreen
import ai.radpretation.opd.ui.screens.FileScreen
import ai.radpretation.opd.ui.screens.EnvironmentScreen
import ai.radpretation.opd.ui.screens.HomeScreen
import ai.radpretation.opd.ui.screens.IntakeScreen
import ai.radpretation.opd.ui.screens.OnboardingScreen
import ai.radpretation.opd.ui.screens.QueueScreen
import ai.radpretation.opd.ui.screens.RemindersScreen
import ai.radpretation.opd.ui.screens.SignInScreen
import ai.radpretation.opd.ui.theme.OpdTheme
import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTag
import androidx.compose.ui.platform.testTag
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.List
import androidx.compose.material.icons.filled.Menu

/**
 * The whole app is one activity and four tabs (doc 04 §3: "bottom-nav 4 tabs
 * Home/My File/Queue/Reminders; works one-handed").
 *
 * The sign-in gate is a *state*, not a route with a back stack: a patient who
 * presses back on the file screen should leave the app, never land on a login
 * form for a session she still has.
 */
class MainActivity : ComponentActivity() {

    private val notificationPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { /* Declined is survivable: the Medicines screen still lists every dose. */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        SyncWorker.enqueue(this)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        setContent { OpdTheme { OpdRoot() } }
    }
}

private sealed class Tab(val route: String, val labelRes: Int, val icon: ImageVector) {
    data object Home : Tab("home", R.string.tab_home, Icons.Filled.Home)
    data object File : Tab("file", R.string.tab_file, Icons.Filled.List)
    data object Queue : Tab("queue", R.string.tab_queue, Icons.Filled.Menu)
    data object Reminders : Tab("reminders", R.string.tab_reminders, Icons.Filled.DateRange)
}

private val tabs = listOf(Tab.Home, Tab.File, Tab.Queue, Tab.Reminders)

@Composable
fun OpdRoot() {
    val context = LocalContext.current
    val container = remember { OpdApp.containerOf(context) }
    val signedIn by container.tokens.signedIn.collectAsState(initial = null)
    var onboarded by remember { mutableStateOf(false) }
    var pairingOpen by remember { mutableStateOf(false) }

    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        when {
            pairingOpen -> EnvironmentScreen(container, onClose = { pairingOpen = false })
            signedIn == null -> Unit // first composition, before DataStore answers
            signedIn == true -> SignedIn(container, onEnvironment = { pairingOpen = true })
            !onboarded -> OnboardingScreen(onDone = { onboarded = true })
            else -> SignInScreen(container, onEnvironment = { pairingOpen = true })
        }
    }
}

@Composable
private fun SignedIn(container: AppContainer, onEnvironment: () -> Unit) {
    val nav = rememberNavController()
    val entry by nav.currentBackStackEntryAsState()
    val current = entry?.destination?.route

    Scaffold(
        bottomBar = {
            // The bar hides on the full-screen flows (intake, and the detail
            // pages reached from Home): doc 04 law 2 — one decision per screen,
            // and an intake question is not a place to offer four tabs.
            if (current in tabs.map { it.route }) {
                NavigationBar {
                    tabs.forEach { tab ->
                        NavigationBarItem(
                            selected = current == tab.route,
                            onClick = {
                                nav.navigate(tab.route) {
                                    popUpTo(Tab.Home.route)
                                    launchSingleTop = true
                                }
                            },
                            icon = { Icon(tab.icon, contentDescription = null) },
                            label = { Text(stringResource(tab.labelRes)) },
                            modifier = Modifier.testTag("tab_${tab.route}"),
                        )
                    }
                }
            }
        },
    ) { padding ->
        Column(Modifier.padding(padding)) {
            OpdNavHost(nav, container, onEnvironment)
        }
    }
}

@Composable
private fun OpdNavHost(
    nav: NavHostController,
    container: AppContainer,
    onEnvironment: () -> Unit,
) {
    NavHost(navController = nav, startDestination = Tab.Home.route) {
        composable(Tab.Home.route) {
            HomeScreen(
                container = container,
                onTalk = { nav.navigate("intake") },
                onCalendar = { nav.navigate("calendar") },
                onFamily = { nav.navigate("family") },
                onAppointments = { nav.navigate("appointments") },
                onQueue = { nav.navigate(Tab.Queue.route) },
                onEnvironment = onEnvironment,
            )
        }
        composable(Tab.File.route) { FileScreen(container) }
        composable(Tab.Queue.route) { QueueScreen(container) }
        composable(Tab.Reminders.route) { RemindersScreen(container) }
        composable("intake") { IntakeScreen(container, onDone = { nav.popBackStack() }) }
        composable("calendar") { CalendarScreen(container, onBack = { nav.popBackStack() }) }
        composable("family") { FamilyScreen(container, onBack = { nav.popBackStack() }) }
        composable("appointments") { AppointmentsScreen(container, onBack = { nav.popBackStack() }) }
    }
}
