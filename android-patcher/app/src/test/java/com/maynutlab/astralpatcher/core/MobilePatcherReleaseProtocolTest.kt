package com.maynutlab.astralpatcher.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MobilePatcherReleaseProtocolTest {
    @Test
    fun parsesTrustedRelease() {
        val parsed = MobilePatcherReleaseProtocol.parse(indexJson())
        assertEquals("1.2.3", parsed.version)
        assertEquals(1_002_003, parsed.versionCode)
        assertTrue(parsed.isNewerThan(1_002_002))
        assertFalse(parsed.isNewerThan(1_002_003))
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsMismatchedVersionCode() {
        MobilePatcherReleaseProtocol.parse(indexJson().replace("1002003", "1002004"))
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsForeignDownloadUrl() {
        MobilePatcherReleaseProtocol.parse(
            indexJson().replace("https://github.com/maynut02", "https://example.test/maynut02")
        )
    }

    private fun indexJson(): String = """
        {
          "version": "1.2.3",
          "versionCode": 1002003,
          "downloadUrl": "https://github.com/maynut02/astral-party-korean-patch/releases/download/android-patcher-v1.2.3/AstralAndroidPatcher.apk",
          "sha256": "${"a".repeat(64)}",
          "size": 123456
        }
    """.trimIndent()
}
