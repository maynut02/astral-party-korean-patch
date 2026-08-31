package com.maynutlab.astralpatcher;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.json.JSONException;
import org.junit.Test;

public final class MobilePatcherReleaseTest {
    @Test
    public void parsesTrustedReleaseIndex() throws Exception {
        MobilePatcherRelease release = MobilePatcherRelease.parse(validIndex());

        assertEquals("0.1.2", release.version);
        assertEquals(1002, release.versionCode);
        assertEquals(1234, release.size);
        assertTrue(release.isNewerThan(1001));
    }

    @Test(expected = JSONException.class)
    public void rejectsMismatchedVersionCode() throws Exception {
        MobilePatcherRelease.parse(validIndex().replace("\"versionCode\": 1002", "\"versionCode\": 1003"));
    }

    @Test(expected = JSONException.class)
    public void rejectsUnexpectedReleaseUrl() throws Exception {
        MobilePatcherRelease.parse(validIndex().replace(
                "android-patcher-v0.1.2/AstralAndroidPatcher.apk",
                "android-patcher-v0.1.2/other.apk"));
    }

    @Test(expected = JSONException.class)
    public void rejectsVersionOutsideAndroidVersionCodeRange() throws Exception {
        MobilePatcherRelease.parse(validIndex()
                .replace("\"version\": \"0.1.2\"", "\"version\": \"2101.0.0\"")
                .replace("android-patcher-v0.1.2/AstralAndroidPatcher.apk",
                        "android-patcher-v2101.0.0/AstralAndroidPatcher.apk"));
    }

    private static String validIndex() {
        return """
                {
                  "version": "0.1.2",
                  "versionCode": 1002,
                  "downloadUrl": "https://github.com/maynut02/astral-party-korean-patch/releases/download/android-patcher-v0.1.2/AstralAndroidPatcher.apk",
                  "sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
                  "size": 1234
                }
                """;
    }
}
