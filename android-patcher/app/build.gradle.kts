plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

val appVersionName = providers.gradleProperty("versionName").orElse("0.1.0").get()

fun versionCodeFrom(version: String): Int {
    val parts = version.split('.')
    require(parts.size == 3) { "versionName must use major.minor.patch" }
    val values = parts.map { part ->
        part.toIntOrNull() ?: error("versionName contains a non-numeric component: $version")
    }
    val (major, minor, patch) = values
    require(major >= 0 && minor in 0..999 && patch in 0..999) {
        "versionName components are outside the supported range: $version"
    }
    val code = major.toLong() * 1_000_000L + minor.toLong() * 1_000L + patch.toLong()
    require(code in 1..2_100_000_000L) { "versionCode is outside Android's supported range" }
    return code.toInt()
}

android {
    namespace = "com.maynutlab.astralpatcher"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.maynutlab.astralpatcher"
        minSdk = 30
        targetSdk = 36
        versionCode = versionCodeFrom(appVersionName)
        versionName = appVersionName
    }

    signingConfigs {
        val keystorePath = System.getenv("ANDROID_KEYSTORE_PATH")
        val keystorePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")
        val keyAliasValue = System.getenv("ANDROID_KEY_ALIAS")
        val keyPasswordValue = System.getenv("ANDROID_KEY_PASSWORD")
        if (!keystorePath.isNullOrBlank()
            && !keystorePassword.isNullOrBlank()
            && !keyAliasValue.isNullOrBlank()
            && !keyPasswordValue.isNullOrBlank()) {
            create("release") {
                storeFile = file(keystorePath)
                storePassword = keystorePassword
                keyAlias = keyAliasValue
                keyPassword = keyPasswordValue
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            signingConfig = signingConfigs.findByName("release")
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    buildFeatures {
        aidl = true
        buildConfig = true
        compose = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

}

dependencies {
    // Compose 1.11 is the newest stable line that compiles against API 36.
    val composeBom = platform("androidx.compose:compose-bom:2026.04.01")
    implementation(composeBom)

    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.core:core:1.17.0")

    implementation("dev.rikka.shizuku:api:13.1.5")
    implementation("dev.rikka.shizuku:provider:13.1.5")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20260814")
}
