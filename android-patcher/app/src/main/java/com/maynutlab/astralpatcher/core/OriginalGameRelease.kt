package com.maynutlab.astralpatcher.core

import org.json.JSONObject
import java.util.Locale

data class OriginalGameApk(
    val name: String,
    val splitName: String?,
    val downloadUrl: String,
    val sha256: String,
    val size: Long,
)

data class OriginalGameRelease(
    val versionName: String,
    val versionCode: Long,
    val certificateSha256: String,
    val deviceProfile: String,
    val releaseTag: String,
    val files: List<OriginalGameApk>,
)

object OriginalGameReleaseProtocol {
    private const val REPOSITORY = "maynut02/astral-party-korean-patch"
    private const val MAX_APK_FILES = 64
    private const val MAX_VERSION_NAME_CHARS = 128
    private const val MAX_DEVICE_PROFILE_CHARS = 64
    private val SAFE_APK_NAME = Regex("^[A-Za-z0-9._-]+\\.apk$")
    private val SAFE_SPLIT_NAME = Regex("^[A-Za-z0-9._-]+$")

    fun parse(json: String): OriginalGameRelease {
        val root = JSONObject(json)
        require(root.getInt("schemaVersion") == 1) {
            "지원하지 않는 원본 게임 APK index입니다."
        }
        require(root.getString("packageName") == GAME_PACKAGE) {
            "원본 게임 APK packageName이 다릅니다."
        }
        val versionName = root.getString("versionName")
        require(versionName.isNotBlank() && versionName.length <= MAX_VERSION_NAME_CHARS) {
            "원본 게임 versionName이 올바르지 않습니다."
        }
        val versionCode = root.getLong("versionCode")
        require(versionCode > 0) { "원본 게임 versionCode가 올바르지 않습니다." }
        val certificateSha256 = requireSha256(root.getString("certificateSha256"))
        val deviceProfile = root.getString("deviceProfile")
        require(
            deviceProfile.isNotBlank() &&
                deviceProfile.length <= MAX_DEVICE_PROFILE_CHARS &&
                SAFE_SPLIT_NAME.matches(deviceProfile)
        ) {
            "원본 게임 device profile이 올바르지 않습니다."
        }
        val releaseTag = root.getString("releaseTag")
        require(releaseTag == "android-game-v$versionCode") {
            "원본 게임 release tag가 versionCode와 일치하지 않습니다."
        }

        val rawFiles = root.getJSONArray("files")
        require(rawFiles.length() in 1..MAX_APK_FILES) {
            "원본 게임 APK 파일 수가 올바르지 않습니다."
        }
        val names = mutableSetOf<String>()
        val splitNames = mutableSetOf<String>()
        var baseCount = 0
        val files = buildList {
            for (index in 0 until rawFiles.length()) {
                val item = rawFiles.getJSONObject(index)
                val name = item.getString("name")
                require(SAFE_APK_NAME.matches(name) && names.add(name)) {
                    "원본 게임 APK 파일 이름이 안전하지 않거나 중복되었습니다: $name"
                }
                val splitName = if (item.isNull("splitName")) {
                    baseCount += 1
                    null
                } else {
                    item.getString("splitName").also {
                        require(SAFE_SPLIT_NAME.matches(it) && splitNames.add(it)) {
                            "원본 게임 split name이 안전하지 않거나 중복되었습니다: $it"
                        }
                    }
                }
                if (splitName == null) require(name == "base.apk") {
                    "원본 게임 base APK 이름이 올바르지 않습니다."
                }
                val downloadUrl = item.getString("downloadUrl")
                val expectedUrl = "https://github.com/$REPOSITORY/releases/download/" +
                    "$releaseTag/$name"
                require(downloadUrl == expectedUrl) {
                    "신뢰하지 않는 원본 게임 APK 주소입니다."
                }
                TrustedUrls.requireReleaseAsset(downloadUrl)
                add(
                    OriginalGameApk(
                        name = name,
                        splitName = splitName,
                        downloadUrl = downloadUrl,
                        sha256 = requireSha256(item.getString("sha256")),
                        size = item.getLong("size").also {
                            require(it > 0) { "원본 게임 APK 크기가 올바르지 않습니다." }
                        },
                    )
                )
            }
        }
        require(baseCount == 1) { "원본 게임 base APK가 정확히 하나가 아닙니다." }
        require(files.first().splitName == null) { "원본 게임 base APK가 파일 목록의 첫 항목이 아닙니다." }
        return OriginalGameRelease(
            versionName = versionName,
            versionCode = versionCode,
            certificateSha256 = certificateSha256,
            deviceProfile = deviceProfile,
            releaseTag = releaseTag,
            files = files,
        )
    }

    private fun requireSha256(value: String): String {
        val normalized = value.lowercase(Locale.ROOT)
        require(normalized.length == 64 && normalized.all { it in '0'..'9' || it in 'a'..'f' }) {
            "원본 게임 APK SHA-256이 올바르지 않습니다."
        }
        return normalized
    }
}
