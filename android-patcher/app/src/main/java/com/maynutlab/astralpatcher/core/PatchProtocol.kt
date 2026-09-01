package com.maynutlab.astralpatcher.core

import org.json.JSONObject
import java.net.URI
import java.security.MessageDigest
import java.util.Locale

const val GAME_PACKAGE = "com.feimo.astralpartyjpn"
const val SHIZUKU_PACKAGE = "moe.shizuku.privileged.api"
const val PATCH_ROUTE = "INT_ANDROID"
const val PATCH_CHANNEL = "release"
const val RELEASE_INDEX_URL =
    "https://raw.githubusercontent.com/maynut02/astral-party-korean-patch/distribution/release-index.json"
const val SHIZUKU_RELEASE_API =
    "https://api.github.com/repos/thedjchi/Shizuku/releases/latest"
const val MOBILE_PATCHER_INDEX_URL =
    "https://raw.githubusercontent.com/maynut02/astral-party-korean-patch/distribution/mobile-patcher-index.json"

data class CatalogIdentity(
    val gameVersion: String,
    val catalogHash: String,
    val installedPatchVersion: String?,
)

data class ReleaseEntry(
    val gameVersion: String,
    val revision: String,
    val catalogHash: String,
    val patchVersion: String,
    val manifestUrl: String,
    val manifestSha256: String,
)

data class PatchFile(
    val relativePath: String,
    val downloadUrl: String,
    val downloadSha256: String,
    val downloadSize: Long,
    val payloadSha256: String,
    val payloadSize: Long,
    val sourceSha256: String,
    val sourceSize: Long,
)

data class PatchManifest(
    val patchVersion: String,
    val gameVersion: String,
    val revision: String,
    val catalogHash: String,
    val files: List<PatchFile>,
)

data class PatchTargetInspection(
    val total: Int,
    val ready: Int,
    val missing: Int,
    val incompatible: Int,
) {
    val isReady: Boolean
        get() = total > 0 && ready == total && missing == 0 && incompatible == 0
}

object PatchProtocol {
    fun parseInspection(json: String): CatalogIdentity {
        val root = JSONObject(json)
        require(root.getInt("schemaVersion") == 1) { "지원하지 않는 게임 진단 결과입니다." }
        return CatalogIdentity(
            gameVersion = root.getString("gameVersion"),
            catalogHash = requireHex(root.getString("catalogHash"), 32, "catalog hash"),
            installedPatchVersion = root.optString("patchVersion").takeIf { it.isNotBlank() },
        )
    }

    fun resolveRelease(json: String, catalog: CatalogIdentity): ReleaseEntry? {
        val root = JSONObject(json)
        require(root.getInt("schemaVersion") == 1) { "지원하지 않는 release index입니다." }
        val releases = root.getJSONArray("releases")
        var matched: ReleaseEntry? = null
        for (index in 0 until releases.length()) {
            val item = releases.getJSONObject(index)
            if (item.getString("route") != PATCH_ROUTE ||
                item.getString("channel") != PATCH_CHANNEL ||
                item.getString("gameVersion") != catalog.gameVersion ||
                item.getString("catalogHash").lowercase(Locale.ROOT) != catalog.catalogHash
            ) {
                continue
            }
            check(matched == null) { "현재 게임에 일치하는 정식 Android patch가 중복되어 있습니다." }
            val manifestUrl = item.getString("manifestUrl")
            TrustedUrls.requireReleaseAsset(manifestUrl)
            matched = ReleaseEntry(
                gameVersion = item.getString("gameVersion"),
                revision = item.getString("revision"),
                catalogHash = requireHex(item.getString("catalogHash"), 32, "catalog hash"),
                patchVersion = item.getString("patchVersion").also {
                    require(it.isNotBlank()) { "patch version이 비어 있습니다." }
                },
                manifestUrl = manifestUrl,
                manifestSha256 = requireHex(
                    item.getString("manifestSha256"), 64, "manifest SHA-256"
                ),
            )
        }
        return matched
    }

    fun parseManifest(bytes: ByteArray, release: ReleaseEntry): PatchManifest {
        require(sha256(bytes) == release.manifestSha256) {
            "manifest SHA-256이 release index와 일치하지 않습니다."
        }
        val root = JSONObject(bytes.toString(Charsets.UTF_8))
        require(root.getInt("schemaVersion") == 2) { "지원하지 않는 patch manifest입니다." }
        val patch = root.getJSONObject("patch")
        val game = root.getJSONObject("game")
        require(patch.getString("route") == PATCH_ROUTE) { "Android용 patch가 아닙니다." }
        require(patch.getString("channel") == PATCH_CHANNEL) { "정식 patch가 아닙니다." }
        require(patch.getString("version") == release.patchVersion) { "patch version이 다릅니다." }
        require(game.getString("version") == release.gameVersion) { "게임 버전이 다릅니다." }
        require(game.getString("revision") == release.revision) { "게임 revision이 다릅니다." }
        require(requireHex(game.getString("catalogHash"), 32, "catalog hash") == release.catalogHash) {
            "catalog hash가 다릅니다."
        }

        val rawFiles = root.getJSONArray("files")
        require(rawFiles.length() > 0) { "patch 파일이 없습니다." }
        require(rawFiles.length() <= MAX_PATCH_FILES) { "patch 파일 수가 허용 범위를 초과합니다." }
        val identities = mutableSetOf<String>()
        val files = buildList {
            for (index in 0 until rawFiles.length()) {
                val item = rawFiles.getJSONObject(index)
                require(item.getString("target") == "addressables") {
                    "Android Patch Manager는 APK 내부 파일을 수정하지 않습니다."
                }
                require(item.getString("operation") == "replace") { "지원하지 않는 patch 작업입니다." }
                require(item.getString("compression") == "gzip") { "지원하지 않는 압축 형식입니다." }
                val path = safeRelativePath(item.getString("path"))
                require(identities.add(path)) { "중복된 patch 경로입니다: $path" }
                val downloadUrl = item.getString("downloadUrl")
                TrustedUrls.requireReleaseAsset(downloadUrl)
                add(
                    PatchFile(
                        relativePath = path,
                        downloadUrl = downloadUrl,
                        downloadSha256 = requireHex(
                            item.getString("downloadSha256"), 64, "download SHA-256"
                        ),
                        downloadSize = positive(item.getLong("downloadSize"), "download size"),
                        payloadSha256 = requireHex(item.getString("sha256"), 64, "payload SHA-256"),
                        payloadSize = positive(item.getLong("size"), "payload size"),
                        sourceSha256 = requireHex(
                            item.getString("sourceSha256"), 64, "source SHA-256"
                        ),
                        sourceSize = positive(item.getLong("sourceSize"), "source size"),
                    )
                )
            }
        }
        return PatchManifest(
            patchVersion = release.patchVersion,
            gameVersion = release.gameVersion,
            revision = release.revision,
            catalogHash = release.catalogHash,
            files = files,
        )
    }

    fun createTargetInspectionRequest(manifest: PatchManifest): String {
        require(manifest.files.isNotEmpty() && manifest.files.size <= MAX_PATCH_FILES) {
            "검사할 patch 파일 수가 올바르지 않습니다."
        }
        val files = org.json.JSONArray()
        manifest.files.forEach { item ->
            files.put(
                JSONObject()
                    .put("path", safeRelativePath(item.relativePath))
                    .put("sourceSize", item.sourceSize)
                    .put("sourceSha256", item.sourceSha256)
                    .put("payloadSize", item.payloadSize)
                    .put("payloadSha256", item.payloadSha256)
            )
        }
        val request = JSONObject()
            .put("schemaVersion", 1)
            .put("catalogHash", manifest.catalogHash)
            .put("patchVersion", manifest.patchVersion)
            .put("files", files)
            .toString()
        require(request.toByteArray(Charsets.UTF_8).size <= MAX_INSPECTION_REQUEST_BYTES) {
            "리소스 검사 요청이 허용 크기를 초과합니다."
        }
        return request
    }

    fun parseTargetInspection(json: String): PatchTargetInspection {
        val root = JSONObject(json)
        require(root.getInt("schemaVersion") == 1) { "지원하지 않는 리소스 검사 결과입니다." }
        val result = PatchTargetInspection(
            total = root.getInt("total"),
            ready = root.getInt("ready"),
            missing = root.getInt("missing"),
            incompatible = root.getInt("incompatible"),
        )
        require(result.total in 1..MAX_PATCH_FILES) { "리소스 검사 대상 수가 올바르지 않습니다." }
        require(result.ready >= 0 && result.missing >= 0 && result.incompatible >= 0) {
            "리소스 검사 결과에 음수 값이 있습니다."
        }
        require(result.ready + result.missing + result.incompatible == result.total) {
            "리소스 검사 결과 합계가 올바르지 않습니다."
        }
        return result
    }

    fun safeRelativePath(value: String): String {
        val normalized = value.replace('\\', '/')
        require(
            normalized.isNotBlank() &&
                normalized.length <= MAX_RELATIVE_PATH_CHARS &&
                !normalized.startsWith('/')
        ) {
            "안전하지 않은 Addressables 경로입니다."
        }
        require(normalized.split('/').all { it.isNotBlank() && it != "." && it != ".." }) {
            "안전하지 않은 Addressables 경로입니다."
        }
        return normalized
    }

    fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).toHex()

    private fun requireHex(value: String, length: Int, label: String): String {
        val normalized = value.lowercase(Locale.ROOT)
        require(normalized.length == length && normalized.all { it in '0'..'9' || it in 'a'..'f' }) {
            "$label 값이 올바르지 않습니다."
        }
        return normalized
    }

    private fun positive(value: Long, label: String): Long {
        require(value > 0) { "$label 값이 올바르지 않습니다." }
        return value
    }

    private const val MAX_PATCH_FILES = 2_048
    private const val MAX_RELATIVE_PATH_CHARS = 1_024
    private const val MAX_INSPECTION_REQUEST_BYTES = 256 * 1_024
}

object TrustedUrls {
    private const val REPOSITORY_PATH = "/maynut02/astral-party-korean-patch/"
    private const val SHIZUKU_RELEASE_PATH = "/thedjchi/Shizuku/releases/download/"

    fun requireIndex(value: String) {
        require(value == RELEASE_INDEX_URL) { "신뢰하지 않는 release index 주소입니다." }
    }

    fun requireReleaseAsset(value: String) {
        val uri = parseHttps(value)
        require(uri.host.equals("github.com", ignoreCase = true)) { "신뢰하지 않는 patch host입니다." }
        require(uri.path.startsWith("${REPOSITORY_PATH}releases/download/")) {
            "신뢰하지 않는 patch 경로입니다."
        }
        require(uri.rawQuery == null && uri.rawFragment == null) { "patch URL에 query나 fragment를 사용할 수 없습니다." }
    }

    fun requireShizukuApi(value: String) {
        require(value == SHIZUKU_RELEASE_API) { "신뢰하지 않는 Shizuku API 주소입니다." }
    }

    fun requireMobilePatcherIndex(value: String) {
        require(value == MOBILE_PATCHER_INDEX_URL) {
            "신뢰하지 않는 AndroidPatcher index 주소입니다."
        }
    }

    fun requireShizukuReleaseAsset(value: String) {
        val uri = parseHttps(value)
        require(uri.host.equals("github.com", ignoreCase = true)) {
            "신뢰하지 않는 Shizuku 다운로드 host입니다."
        }
        require(uri.path.startsWith(SHIZUKU_RELEASE_PATH)) {
            "신뢰하지 않는 Shizuku 다운로드 경로입니다."
        }
        require(uri.rawQuery == null && uri.rawFragment == null) {
            "Shizuku 다운로드 URL에 query나 fragment를 사용할 수 없습니다."
        }
    }

    fun isTrustedRedirect(value: String): Boolean {
        val uri = runCatching { parseHttps(value) }.getOrNull() ?: return false
        val host = uri.host.lowercase(Locale.ROOT)
        return host == "github.com" ||
            host == "api.github.com" ||
            host == "release-assets.githubusercontent.com" ||
            host.endsWith(".githubusercontent.com")
    }

    private fun parseHttps(value: String): URI {
        val uri = URI(value)
        require(uri.scheme.equals("https", ignoreCase = true) && !uri.host.isNullOrBlank()) {
            "HTTPS URL만 사용할 수 있습니다."
        }
        return uri
    }
}

private fun ByteArray.toHex(): String =
    joinToString(separator = "") { "%02x".format(it.toInt() and 0xff) }
