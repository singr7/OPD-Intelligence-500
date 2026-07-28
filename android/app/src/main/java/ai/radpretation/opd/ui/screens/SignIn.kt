package ai.radpretation.opd.ui.screens

import ai.radpretation.opd.AppContainer
import ai.radpretation.opd.R
import ai.radpretation.opd.data.ApiException
import ai.radpretation.opd.data.OfflineException
import ai.radpretation.opd.ui.BigButton
import ai.radpretation.opd.ui.DharaAvatar
import ai.radpretation.opd.ui.Muted
import ai.radpretation.opd.ui.QuietButton
import ai.radpretation.opd.ui.WarningStamp
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

/**
 * Phone, then a 6-digit code. That is the entire sign-up too (doc 03 §1c.7:
 * "SMS-based OTP login") — there is no account to create, because the hospital
 * already knows who she is.
 *
 * The trust line (doc 04 law 10) is on screen before she types anything.
 */
@Composable
fun SignInScreen(container: AppContainer, onEnvironment: () -> Unit = {}) {
    var phone by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var codeSent by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    val offlineText = stringResource(R.string.offline_banner)
    val failedText = stringResource(R.string.signin_failed)
    val genericText = stringResource(R.string.error_generic)

    fun run(block: suspend () -> Unit) {
        busy = true
        error = null
        scope.launch {
            try {
                block()
            } catch (_: OfflineException) {
                error = offlineText
            } catch (e: ApiException) {
                error = if (e.code == 401) failedText else genericText
            } catch (_: Exception) {
                error = genericText
            } finally {
                busy = false
            }
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Spacer(Modifier.height(32.dp))
        DharaAvatar(size = 96.dp, thinking = busy)
        Spacer(Modifier.height(28.dp))

        Text(
            stringResource(if (codeSent) R.string.signin_code_title else R.string.signin_title),
            style = MaterialTheme.typography.headlineMedium,
        )
        Spacer(Modifier.height(10.dp))
        Muted(
            if (codeSent) {
                stringResource(R.string.signin_code_help, phone)
            } else {
                stringResource(R.string.signin_help)
            },
        )
        Spacer(Modifier.height(24.dp))

        if (!codeSent) {
            OutlinedTextField(
                value = phone,
                onValueChange = { phone = it.filter { c -> c.isDigit() || c == '+' }.take(15) },
                label = { Text(stringResource(R.string.signin_phone)) },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
                singleLine = true,
                modifier = Modifier.fillMaxWidth().heightIn(min = 64.dp).testTag("phone_field"),
            )
            Spacer(Modifier.height(20.dp))
            BigButton(
                text = stringResource(R.string.signin_send),
                enabled = phone.length >= 8 && !busy,
                onClick = {
                    run {
                        container.auth.requestOtp(phone)
                        codeSent = true
                    }
                },
                modifier = Modifier.testTag("send_code"),
            )
        } else {
            OutlinedTextField(
                value = code,
                onValueChange = { code = it.filter(Char::isDigit).take(6) },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                singleLine = true,
                modifier = Modifier.fillMaxWidth().heightIn(min = 64.dp).testTag("code_field"),
            )
            Spacer(Modifier.height(20.dp))
            BigButton(
                text = stringResource(R.string.signin_verify),
                enabled = code.length >= 4 && !busy,
                onClick = { run { container.auth.verifyOtp(phone, code) } },
                modifier = Modifier.testTag("verify_code"),
            )
            Spacer(Modifier.height(12.dp))
            QuietButton(
                text = stringResource(R.string.signin_resend),
                enabled = !busy,
                onClick = { run { container.auth.requestOtp(phone) } },
            )
            Spacer(Modifier.height(12.dp))
            QuietButton(
                text = stringResource(R.string.cancel),
                onClick = { codeSent = false; code = "" },
            )
        }

        error?.let {
            Spacer(Modifier.height(20.dp))
            WarningStamp(it, Modifier.testTag("signin_error"))
        }

        Spacer(Modifier.height(28.dp))
        Muted(stringResource(R.string.signin_trust))
        Spacer(Modifier.height(12.dp))
        QuietButton(
            text = stringResource(R.string.environment_open),
            onClick = onEnvironment,
            modifier = Modifier.testTag("environment_open"),
        )
    }
}
