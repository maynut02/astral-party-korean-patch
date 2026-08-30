package io.github.maynut02.astralpatcher;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.Locale;

final class ShizukuRelease {
    static final String REPOSITORY = "thedjchi/Shizuku";
    static final String PACKAGE_NAME = "moe.shizuku.privileged.api";

    final String version;
    final String downloadUrl;
    final String sha256;
    final long size;

    private ShizukuRelease(String version, String downloadUrl, String sha256, long size) {
        this.version = version;
        this.downloadUrl = downloadUrl;
        this.sha256 = sha256;
        this.size = size;
    }

    static ShizukuRelease parseLatest(String json) throws JSONException {
        JSONObject release = new JSONObject(json);
        if (release.optBoolean("draft") || release.optBoolean("prerelease")) {
            throw new JSONException("GitHub latest release가 안정 버전이 아닙니다.");
        }

        JSONArray assets = release.getJSONArray("assets");
        JSONObject selected = null;
        for (int index = 0; index < assets.length(); index++) {
            JSONObject asset = assets.getJSONObject(index);
            String lower = asset.getString("name").toLowerCase(Locale.ROOT);
            if (!lower.endsWith(".apk") || lower.contains("debug") || lower.contains("beta")) {
                continue;
            }
            if (selected != null) {
                throw new JSONException("안정 APK asset이 여러 개라 자동 선택할 수 없습니다.");
            }
            selected = asset;
        }

        if (selected == null) {
            throw new JSONException("최신 Shizuku release에서 안정 APK asset을 찾지 못했습니다.");
        }

        String downloadUrl = selected.getString("browser_download_url");
        String expectedPrefix = "https://github.com/" + REPOSITORY + "/releases/download/";
        if (!downloadUrl.startsWith(expectedPrefix)) {
            throw new JSONException("신뢰하지 않는 Shizuku 다운로드 주소입니다.");
        }

        String digest = selected.optString("digest", "");
        if (!digest.startsWith("sha256:")) {
            throw new JSONException("Shizuku release asset에 SHA-256 digest가 없습니다.");
        }
        String sha256 = digest.substring("sha256:".length()).toLowerCase(Locale.ROOT);
        if (!sha256.matches("[0-9a-f]{64}")) {
            throw new JSONException("Shizuku release asset의 SHA-256 digest가 잘못되었습니다.");
        }

        return new ShizukuRelease(
                release.getString("tag_name"),
                downloadUrl,
                sha256,
                selected.getLong("size"));
    }
}
