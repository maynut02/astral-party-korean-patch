package io.github.maynut02.astralpatcher;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Locale;

final class DistributionIndex {
    static final String PACKAGE_NAME = "com.feimo.astralpartyjpn";
    static final String INSTALLER_PACKAGE = "com.android.vending";

    final String gameVersion;
    final String downloadUrl;
    final String sha256;
    final long size;

    private DistributionIndex(String gameVersion, String downloadUrl, String sha256, long size) {
        this.gameVersion = gameVersion;
        this.downloadUrl = downloadUrl;
        this.sha256 = sha256;
        this.size = size;
    }

    static DistributionIndex parse(String json) throws JSONException {
        JSONObject root = new JSONObject(json);
        if (root.getInt("schemaVersion") != 1) {
            throw new JSONException("지원하지 않는 Android APK index 버전입니다.");
        }
        if (!PACKAGE_NAME.equals(root.getString("packageName"))) {
            throw new JSONException("예상하지 못한 게임 packageName입니다.");
        }
        if (!INSTALLER_PACKAGE.equals(root.getString("installerPackageName"))) {
            throw new JSONException("예상하지 못한 installerPackageName입니다.");
        }

        String gameVersion = root.getString("gameVersion");
        String downloadUrl = root.getString("downloadUrl");
        String expectedUrl = "https://github.com/maynut02/astral-party-korean-patch/releases/download/"
                + "int-apk-v" + gameVersion + "/AstralPartyKorean.apk";
        if (!downloadUrl.equals(expectedUrl)) {
            throw new JSONException("신뢰하지 않는 게임 APK 다운로드 주소입니다.");
        }

        String sha256 = root.getString("sha256").toLowerCase(Locale.ROOT);
        if (!sha256.matches("[0-9a-f]{64}")) {
            throw new JSONException("잘못된 SHA-256 값입니다.");
        }

        long size = root.getLong("size");
        if (size <= 0) {
            throw new JSONException("잘못된 APK 크기입니다.");
        }

        return new DistributionIndex(gameVersion, downloadUrl, sha256, size);
    }
}
