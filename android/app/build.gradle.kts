plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ksp)
}

android {
    namespace = "ai.radpretation.opd"
    compileSdk = 35

    defaultConfig {
        applicationId = "ai.radpretation.opd"
        // Android 8.0 (doc 03 §1c.7). The pilot's patients carry the phones the
        // Alwar market sold five years ago, not this year's.
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0-s16"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // The API base is a build input, not a hardcoded string: the emulator
        // reaches the dev box at 10.0.2.2, a real phone reaches the pilot at
        // opd.radpretation.ai, and a test reaches MockWebServer on localhost.
        buildConfigField(
            "String",
            "API_BASE_URL",
            "\"${project.findProperty("opdApiBase") ?: "https://opd.radpretation.ai"}\"",
        )

        // One language pack per pilot tongue, and no others shipped (doc 03 §1).
        resourceConfigurations += setOf("en", "hi", "mr", "te")
    }

    buildTypes {
        release {
            // Both on: R8 plus resource shrinking is most of what keeps the APK
            // under the 15MB line the spec draws.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // Unsigned is fine — nothing here publishes. The size gate in CI
            // measures this artifact.
        }
        debug {
            applicationIdSuffix = ".debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        // Room's generated code and java.time on API 26 both want this.
        isCoreLibraryDesugaringEnabled = false
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
    }

    packaging {
        resources {
            excludes += setOf(
                "/META-INF/{AL2.0,LGPL2.1}",
                "/META-INF/*.version",
                "DebugProbesKt.bin",
                "kotlin-tooling-metadata.json",
            )
        }
    }

    testOptions {
        unitTests {
            isIncludeAndroidResources = true
            isReturnDefaultValues = true
        }
    }

    ksp {
        arg("room.schemaLocation", "$projectDir/schemas")
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.material3)
    debugImplementation(libs.androidx.compose.ui.tooling)
    implementation(libs.androidx.compose.ui.tooling.preview)

    implementation(libs.androidx.navigation.compose)

    implementation(libs.androidx.room.runtime)
    implementation(libs.androidx.room.ktx)
    ksp(libs.androidx.room.compiler)

    implementation(libs.androidx.work.runtime)
    implementation(libs.androidx.datastore.preferences)

    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)

    testImplementation(libs.junit)
    testImplementation(libs.robolectric)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.androidx.junit)

    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.test.runner)
    androidTestImplementation(libs.androidx.test.rules)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(libs.androidx.work.testing)
    androidTestImplementation(libs.okhttp.mockwebserver)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
}

/**
 * The size gate doc 06 asks for ("APK size check in CI").
 *
 * It fails the build rather than printing a warning, because an APK that
 * quietly crossed 15MB is one a patient on a 2GB phone silently stops being
 * able to install — a failure nobody reports and everybody suffers.
 */
val apkSizeLimitBytes = 15L * 1024 * 1024

tasks.register("checkApkSize") {
    group = "verification"
    description = "Fails if the release APK is over 15MB (doc 03 §1c.7)."
    dependsOn("assembleRelease")

    val apkDir = layout.buildDirectory.dir("outputs/apk/release")
    doLast {
        val apk = apkDir.get().asFile.listFiles { f -> f.extension == "apk" }?.minByOrNull { it.length() }
            ?: error("no release APK found — did assembleRelease run?")
        val size = apk.length()
        val mb = "%.2f".format(size / 1024.0 / 1024.0)
        if (size > apkSizeLimitBytes) {
            error("$mb MB exceeds the 15MB budget (doc 03 §1c.7): ${apk.name}")
        }
        logger.lifecycle("APK size OK: $mb MB of 15MB (${apk.name})")
    }
}
