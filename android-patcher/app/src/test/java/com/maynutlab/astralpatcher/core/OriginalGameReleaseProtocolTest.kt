package com.maynutlab.astralpatcher.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class OriginalGameReleaseProtocolTest {
    @Test
    fun parsesTrustedSplitRelease() {
        val parsed = OriginalGameReleaseProtocol.parse(indexJson())
        assertEquals("1.2.3", parsed.versionName)
        assertEquals(1234L, parsed.versionCode)
        assertEquals("px_9a", parsed.deviceProfile)
        assertEquals(2, parsed.files.size)
        assertNull(parsed.files.first().splitName)
        assertEquals("config.arm64_v8a", parsed.files.last().splitName)
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsForeignAssetUrl() {
        OriginalGameReleaseProtocol.parse(
            indexJson().replace("https://github.com/maynut02", "https://example.test/maynut02")
        )
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsMissingBaseApk() {
        OriginalGameReleaseProtocol.parse(
            indexJson().replace("\"splitName\": null", "\"splitName\": \"config.base\"")
        )
    }

    private fun indexJson(): String = """
        {
          "schemaVersion": 1,
          "packageName": "com.feimo.astralpartyjpn",
          "versionName": "1.2.3",
          "versionCode": 1234,
          "certificateSha256": "${"a".repeat(64)}",
          "deviceProfile": "px_9a",
          "releaseTag": "android-game-v1234",
          "files": [
            {
              "name": "base.apk",
              "splitName": null,
              "downloadUrl": "https://github.com/maynut02/astral-party-korean-patch/releases/download/android-game-v1234/base.apk",
              "sha256": "${"b".repeat(64)}",
              "size": 100
            },
            {
              "name": "split-001-config.arm64_v8a.apk",
              "splitName": "config.arm64_v8a",
              "downloadUrl": "https://github.com/maynut02/astral-party-korean-patch/releases/download/android-game-v1234/split-001-config.arm64_v8a.apk",
              "sha256": "${"c".repeat(64)}",
              "size": 200
            }
          ]
        }
    """.trimIndent()
}
