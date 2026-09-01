package com.maynutlab.astralpatcher.core

import com.maynutlab.astralpatcher.BuildConfig
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.zip.GZIPInputStream

object PatchHttpClient {
    private const val CONNECT_TIMEOUT_MS = 15_000
    private const val READ_TIMEOUT_MS = 60_000
    private const val MAX_REDIRECTS = 5
    private const val BUFFER_SIZE = 128 * 1024
    private const val MAX_METADATA_BYTES = 4 * 1024 * 1024

    fun getIndex(): String {
        TrustedUrls.requireIndex(RELEASE_INDEX_URL)
        return getBytes(RELEASE_INDEX_URL, MAX_METADATA_BYTES).toString(Charsets.UTF_8)
    }

    fun getManifest(release: ReleaseEntry): ByteArray =
        getBytes(release.manifestUrl, MAX_METADATA_BYTES)

    fun getLatestShizukuRelease(): ShizukuRelease {
        TrustedUrls.requireShizukuApi(SHIZUKU_RELEASE_API)
        val json = getBytes(SHIZUKU_RELEASE_API, MAX_METADATA_BYTES).toString(Charsets.UTF_8)
        return ShizukuReleaseProtocol.parseLatest(json)
    }

    fun getLatestMobilePatcherRelease(): MobilePatcherRelease {
        TrustedUrls.requireMobilePatcherIndex(MOBILE_PATCHER_INDEX_URL)
        val json = getBytes(MOBILE_PATCHER_INDEX_URL, MAX_METADATA_BYTES).toString(Charsets.UTF_8)
        return MobilePatcherReleaseProtocol.parse(json)
    }

    fun downloadShizukuApk(
        cacheDir: File,
        release: ShizukuRelease,
        onProgress: (Int) -> Unit,
    ): File {
        TrustedUrls.requireShizukuReleaseAsset(release.downloadUrl)
        return downloadVerifiedApk(
            cacheDir,
            "shizuku-latest.apk",
            release.downloadUrl,
            release.size,
            release.sha256,
            onProgress,
        )
    }

    fun downloadMobilePatcherApk(
        cacheDir: File,
        release: MobilePatcherRelease,
        onProgress: (Int) -> Unit,
    ): File {
        TrustedUrls.requireReleaseAsset(release.downloadUrl)
        return downloadVerifiedApk(
            cacheDir,
            "astral-android-patcher-${release.version}.apk",
            release.downloadUrl,
            release.size,
            release.sha256,
            onProgress,
        )
    }

    fun downloadPayload(
        cacheDir: File,
        item: PatchFile,
        ordinal: Int,
        onProgress: (Int) -> Unit,
    ): File {
        val root = File(cacheDir, "patch-payloads").apply { mkdirs() }
        val transport = File(root, "$ordinal.transport")
        val payload = File(root, "$ordinal.payload")
        transport.delete()
        payload.delete()
        try {
            downloadTo(transport, item.downloadUrl, item.downloadSize, item.downloadSha256, onProgress)
            gunzipVerified(transport, payload, item.payloadSize, item.payloadSha256)
            return payload
        } catch (error: Exception) {
            payload.delete()
            throw error
        } finally {
            transport.delete()
        }
    }

    fun downloadOriginal(
        cacheDir: File,
        item: PatchFile,
        ordinal: Int,
        onProgress: (Int) -> Unit,
    ): File {
        val root = File(cacheDir, "patch-originals").apply { mkdirs() }
        val transport = File(root, "$ordinal.transport")
        val payload = File(root, "$ordinal.original")
        transport.delete()
        payload.delete()
        try {
            downloadTo(
                transport,
                item.sourceDownloadUrl,
                item.sourceDownloadSize,
                item.sourceDownloadSha256,
                onProgress,
            )
            gunzipVerified(transport, payload, item.sourceSize, item.sourceSha256)
            return payload
        } catch (error: Exception) {
            payload.delete()
            throw error
        } finally {
            transport.delete()
        }
    }

    private fun getBytes(value: String, maxBytes: Int): ByteArray {
        val connection = open(value)
        try {
            val declared = connection.contentLengthLong
            require(declared <= 0 || declared <= maxBytes) { "metadata가 허용 크기를 초과합니다." }
            BufferedInputStream(connection.inputStream).use { input ->
                val output = ByteArrayOutputStream()
                val buffer = ByteArray(16 * 1024)
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    require(output.size() + read <= maxBytes) { "metadata가 허용 크기를 초과합니다." }
                    output.write(buffer, 0, read)
                }
                return output.toByteArray()
            }
        } finally {
            connection.disconnect()
        }
    }

    private fun downloadVerifiedApk(
        cacheDir: File,
        fileName: String,
        url: String,
        size: Long,
        sha256: String,
        onProgress: (Int) -> Unit,
    ): File {
        val root = File(cacheDir, "downloads").apply { mkdirs() }
        val target = File(root, fileName)
        target.delete()
        try {
            downloadTo(target, url, size, sha256, onProgress)
            return target
        } catch (error: Exception) {
            target.delete()
            throw error
        }
    }

    private fun downloadTo(
        target: File,
        value: String,
        expectedSize: Long,
        expectedSha256: String,
        onProgress: (Int) -> Unit,
    ) {
        val connection = open(value)
        try {
            val declared = connection.contentLengthLong
            require(declared <= 0 || declared == expectedSize) { "다운로드 크기가 manifest와 다릅니다." }
            val digest = MessageDigest.getInstance("SHA-256")
            var total = 0L
            BufferedInputStream(connection.inputStream).use { input ->
                FileOutputStream(target).use { output ->
                    val buffer = ByteArray(BUFFER_SIZE)
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        total += read
                        require(total <= expectedSize) { "다운로드가 manifest 크기를 초과했습니다." }
                        output.write(buffer, 0, read)
                        digest.update(buffer, 0, read)
                        onProgress((total * 100 / expectedSize).toInt().coerceIn(0, 100))
                    }
                    output.fd.sync()
                }
            }
            require(total == expectedSize) { "다운로드 크기가 manifest와 다릅니다." }
            require(digest.digest().toHex() == expectedSha256) { "다운로드 SHA-256이 다릅니다." }
        } finally {
            connection.disconnect()
        }
    }

    private fun gunzipVerified(
        source: File,
        target: File,
        expectedSize: Long,
        expectedSha256: String,
    ) {
        val digest = MessageDigest.getInstance("SHA-256")
        var total = 0L
        GZIPInputStream(BufferedInputStream(FileInputStream(source))).use { input ->
            FileOutputStream(target).use { output ->
                val buffer = ByteArray(BUFFER_SIZE)
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    total += read
                    require(total <= expectedSize) { "압축 해제 결과가 manifest 크기를 초과했습니다." }
                    output.write(buffer, 0, read)
                    digest.update(buffer, 0, read)
                }
                output.fd.sync()
            }
        }
        require(total == expectedSize) { "payload 크기가 manifest와 다릅니다." }
        require(digest.digest().toHex() == expectedSha256) { "payload SHA-256이 다릅니다." }
    }

    private fun open(initial: String): HttpURLConnection {
        var current = initial
        repeat(MAX_REDIRECTS + 1) { redirectCount ->
            val connection = (URL(current).openConnection() as HttpURLConnection).apply {
                connectTimeout = CONNECT_TIMEOUT_MS
                readTimeout = READ_TIMEOUT_MS
                instanceFollowRedirects = false
                setRequestProperty("User-Agent", "AstralPatchManager/${BuildConfig.VERSION_NAME}")
                setRequestProperty("Accept", "application/json, application/octet-stream")
            }
            val status = connection.responseCode
            if (status in 200..299) return connection
            if (status in setOf(301, 302, 303, 307, 308) && redirectCount < MAX_REDIRECTS) {
                val location = connection.getHeaderField("Location")
                    ?: error("redirect 응답에 Location이 없습니다.")
                val redirected = URL(URL(current), location).toString()
                require(TrustedUrls.isTrustedRedirect(redirected)) { "신뢰하지 않는 redirect입니다." }
                connection.disconnect()
                current = redirected
            } else {
                connection.disconnect()
                error("patch 다운로드가 HTTP $status 응답으로 실패했습니다.")
            }
        }
        error("redirect 횟수가 허용 범위를 초과했습니다.")
    }
}

private fun ByteArray.toHex(): String =
    joinToString(separator = "") { "%02x".format(it.toInt() and 0xff) }
