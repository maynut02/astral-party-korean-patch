package com.maynutlab.astralpatcher.core

import org.json.JSONObject
import java.util.Locale

data class MobilePatcherRelease(
    val version: String,
    val versionCode: Int,
    val downloadUrl: String,
    val sha256: String,
    val size: Long,
) {
    fun isNewerThan(currentVersionCode: Int): Boolean = versionCode > currentVersionCode
}

object MobilePatcherReleaseProtocol {
    private const val REPOSITORY = "maynut02/astral-party-korean-patch"

    fun parse(json: String): MobilePatcherRelease {
        val root = JSONObject(json)
        val version = root.getString("version")
        val expectedVersionCode = versionCodeFrom(version)
        val versionCode = root.getInt("versionCode")
        require(versionCode == expectedVersionCode) {
            "AndroidPatcher versionCode가 version과 일치하지 않습니다."
        }
        val downloadUrl = root.getString("downloadUrl")
        val expectedUrl = "https://github.com/$REPOSITORY/releases/download/" +
            "android-patcher-v$version/AstralAndroidPatcher.apk"
        require(downloadUrl == expectedUrl) { "신뢰하지 않는 AndroidPatcher APK 주소입니다." }
        TrustedUrls.requireReleaseAsset(downloadUrl)
        val sha256 = root.getString("sha256").lowercase(Locale.ROOT)
        require(sha256.length == 64 && sha256.all { it in '0'..'9' || it in 'a'..'f' }) {
            "AndroidPatcher APK SHA-256이 올바르지 않습니다."
        }
        val size = root.getLong("size")
        require(size > 0) { "AndroidPatcher APK 크기가 올바르지 않습니다." }
        return MobilePatcherRelease(version, versionCode, downloadUrl, sha256, size)
    }

    private fun versionCodeFrom(version: String): Int {
        require(version.matches(Regex("^[0-9]+\\.[0-9]+\\.[0-9]+$"))) {
            "AndroidPatcher version이 SemVer 형식이 아닙니다."
        }
        val (major, minor, patch) = version.split('.').map { component ->
            component.toLongOrNull() ?: error("AndroidPatcher version 숫자를 해석할 수 없습니다.")
        }
        require(major >= 0 && minor in 0..999 && patch in 0..999) {
            "AndroidPatcher version 범위가 올바르지 않습니다."
        }
        val code = major * 1_000_000L + minor * 1_000L + patch
        require(code in 1..2_100_000_000L) { "AndroidPatcher versionCode 범위가 올바르지 않습니다." }
        return code.toInt()
    }
}
