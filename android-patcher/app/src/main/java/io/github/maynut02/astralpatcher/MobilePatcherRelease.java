package io.github.maynut02.astralpatcher;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Locale;

final class MobilePatcherRelease {
    private static final String REPOSITORY = "maynut02/astral-party-korean-patch";

    final String version;
    final int versionCode;
    final String downloadUrl;
    final String sha256;
    final long size;

    private MobilePatcherRelease(
            String version,
            int versionCode,
            String downloadUrl,
            String sha256,
            long size) {
        this.version = version;
        this.versionCode = versionCode;
        this.downloadUrl = downloadUrl;
        this.sha256 = sha256;
        this.size = size;
    }

    static MobilePatcherRelease parse(String json) throws JSONException {
        JSONObject root = new JSONObject(json);
        String version = root.getString("version");
        int expectedVersionCode = versionCodeFrom(version);
        int versionCode = root.getInt("versionCode");
        if (versionCode != expectedVersionCode) {
            throw new JSONException("AndroidPatcher versionCode가 version과 일치하지 않습니다.");
        }

        String downloadUrl = root.getString("downloadUrl");
        String expectedUrl = "https://github.com/" + REPOSITORY
                + "/releases/download/android-patcher-v" + version
                + "/AstralAndroidPatcher.apk";
        if (!expectedUrl.equals(downloadUrl)) {
            throw new JSONException("신뢰하지 않는 AndroidPatcher APK 다운로드 주소입니다.");
        }

        String sha256 = root.getString("sha256").toLowerCase(Locale.ROOT);
        if (!sha256.matches("[0-9a-f]{64}")) {
            throw new JSONException("잘못된 AndroidPatcher SHA-256 값입니다.");
        }

        long size = root.getLong("size");
        if (size <= 0) {
            throw new JSONException("잘못된 AndroidPatcher APK 크기입니다.");
        }

        return new MobilePatcherRelease(version, versionCode, downloadUrl, sha256, size);
    }

    boolean isNewerThan(int currentVersionCode) {
        return versionCode > currentVersionCode;
    }

    private static int versionCodeFrom(String version) throws JSONException {
        if (!version.matches("[0-9]+\\.[0-9]+\\.[0-9]+")) {
            throw new JSONException("AndroidPatcher version이 SemVer 형식이 아닙니다.");
        }

        String[] parts = version.split("\\.");
        try {
            long major = Long.parseLong(parts[0]);
            long minor = Long.parseLong(parts[1]);
            long patch = Long.parseLong(parts[2]);
            if (major > 2100 || minor > 999 || patch > 999) {
                throw new JSONException("AndroidPatcher version 범위가 올바르지 않습니다.");
            }
            long code = major * 1_000_000L + minor * 1_000L + patch;
            if (code < 1 || code > 2_100_000_000L) {
                throw new JSONException("AndroidPatcher versionCode 범위가 올바르지 않습니다.");
            }
            return (int) code;
        } catch (NumberFormatException error) {
            throw new JSONException("AndroidPatcher version 숫자를 해석할 수 없습니다.");
        }
    }
}
