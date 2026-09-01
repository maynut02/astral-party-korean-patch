package com.maynutlab.astralpatcher.core

import org.json.JSONObject
import java.util.Locale

data class ShizukuRelease(
    val version: String,
    val downloadUrl: String,
    val sha256: String,
    val size: Long,
)

object ShizukuReleaseProtocol {
    fun parseLatest(json: String): ShizukuRelease {
        val release = JSONObject(json)
        require(!release.optBoolean("draft") && !release.optBoolean("prerelease")) {
            "GitHub latest release가 Shizuku 안정 버전이 아닙니다."
        }
        val assets = release.getJSONArray("assets")
        var selected: JSONObject? = null
        for (index in 0 until assets.length()) {
            val candidate = assets.getJSONObject(index)
            val name = candidate.getString("name").lowercase(Locale.ROOT)
            if (!name.endsWith(".apk") || name.contains("debug") || name.contains("beta")) continue
            require(selected == null) { "Shizuku 안정 APK asset이 여러 개라 자동 선택할 수 없습니다." }
            selected = candidate
        }
        val asset = requireNotNull(selected) { "최신 Shizuku release에서 안정 APK를 찾지 못했습니다." }
        val downloadUrl = asset.getString("browser_download_url")
        TrustedUrls.requireShizukuReleaseAsset(downloadUrl)
        val digest = asset.optString("digest")
        require(digest.startsWith("sha256:")) {
            "Shizuku release asset에 SHA-256 digest가 없습니다."
        }
        val sha256 = digest.removePrefix("sha256:").lowercase(Locale.ROOT)
        require(sha256.length == 64 && sha256.all { it in '0'..'9' || it in 'a'..'f' }) {
            "Shizuku release asset의 SHA-256 digest가 올바르지 않습니다."
        }
        val size = asset.getLong("size")
        require(size > 0) { "Shizuku APK 크기가 올바르지 않습니다." }
        val version = release.getString("tag_name")
        require(version.isNotBlank()) { "Shizuku release 버전이 비어 있습니다." }
        return ShizukuRelease(version, downloadUrl, sha256, size)
    }
}
