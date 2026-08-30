package io.github.maynut02.astralpatcher;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.ProgressBar;
import android.widget.TextView;

import androidx.core.content.FileProvider;

import java.io.File;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import rikka.shizuku.Shizuku;

public final class MainActivity extends Activity {
    private static final String SHIZUKU_LATEST_API =
            "https://api.github.com/repos/thedjchi/Shizuku/releases/latest";
    private static final String GAME_INDEX_URL =
            "https://raw.githubusercontent.com/maynut02/astral-party-korean-patch/distribution/android-apk-index.json";
    private static final int SHIZUKU_PERMISSION_REQUEST = 1001;

    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    private TextView shizukuStatus;
    private TextView gameStatus;
    private TextView detailStatus;
    private Button shizukuAction;
    private Button gameAction;
    private Button launchGame;
    private Button refresh;
    private ProgressBar progress;
    private DistributionIndex latestGame;
    private File pendingSystemInstall;

    private final Shizuku.OnBinderReceivedListener binderReceivedListener = this::refreshStatus;
    private final Shizuku.OnBinderDeadListener binderDeadListener = this::refreshStatus;
    private final Shizuku.OnRequestPermissionResultListener permissionResultListener =
            (requestCode, grantResult) -> {
                if (requestCode == SHIZUKU_PERMISSION_REQUEST) {
                    refreshStatus();
                }
            };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        shizukuStatus = findViewById(R.id.shizukuStatus);
        gameStatus = findViewById(R.id.gameStatus);
        detailStatus = findViewById(R.id.detailStatus);
        shizukuAction = findViewById(R.id.shizukuAction);
        gameAction = findViewById(R.id.gameAction);
        launchGame = findViewById(R.id.launchGame);
        refresh = findViewById(R.id.refresh);
        progress = findViewById(R.id.downloadProgress);

        shizukuAction.setOnClickListener(view -> handleShizukuAction());
        gameAction.setOnClickListener(view -> downloadLatestGame());
        launchGame.setOnClickListener(view -> launchGame());
        refresh.setOnClickListener(view -> refreshStatus());

        Shizuku.addBinderReceivedListenerSticky(binderReceivedListener);
        Shizuku.addBinderDeadListener(binderDeadListener);
        Shizuku.addRequestPermissionResultListener(permissionResultListener);
        refreshStatus();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (pendingSystemInstall != null && getPackageManager().canRequestPackageInstalls()) {
            File apk = pendingSystemInstall;
            pendingSystemInstall = null;
            openSystemInstaller(apk);
        }
        refreshStatus();
    }

    @Override
    protected void onDestroy() {
        Shizuku.removeBinderReceivedListener(binderReceivedListener);
        Shizuku.removeBinderDeadListener(binderDeadListener);
        Shizuku.removeRequestPermissionResultListener(permissionResultListener);
        executor.shutdownNow();
        super.onDestroy();
    }

    private void refreshStatus() {
        runOnUiThread(() -> {
            updateShizukuUi();
            updateInstalledGameUi();
        });
        fetchLatestGameIndex();
    }

    private void updateShizukuUi() {
        if (!isPackageInstalled(ShizukuRelease.PACKAGE_NAME)) {
            shizukuStatus.setText("설치되지 않았습니다. GitHub의 최신 안정 버전을 자동으로 받을 수 있습니다.");
            shizukuAction.setText(R.string.shizuku_install);
            return;
        }

        if (!Shizuku.pingBinder()) {
            shizukuStatus.setText("설치됨 · Shizuku 서비스를 시작해야 합니다.");
            shizukuAction.setText(R.string.shizuku_open);
            return;
        }

        if (Shizuku.checkSelfPermission() != PackageManager.PERMISSION_GRANTED) {
            shizukuStatus.setText("실행 중 · Mobile Patcher 권한이 필요합니다.");
            shizukuAction.setText(R.string.shizuku_permission);
            return;
        }

        shizukuStatus.setText("연결됨 · 권한 허용됨");
        shizukuAction.setText(R.string.shizuku_open);
    }

    private void updateInstalledGameUi() {
        String version = installedGameVersion();
        if (version == null) {
            gameStatus.setText("게임 미설치 · 최신 배포 정보를 확인하는 중입니다.");
            launchGame.setVisibility(View.GONE);
        } else {
            gameStatus.setText("설치됨: " + version + " · 최신 배포 정보를 확인하는 중입니다.");
            launchGame.setVisibility(View.VISIBLE);
        }
    }

    private void fetchLatestGameIndex() {
        executor.execute(() -> {
            try {
                DistributionIndex index = DistributionIndex.parse(HttpClient.getText(GAME_INDEX_URL));
                latestGame = index;
                runOnUiThread(() -> {
                    String installed = installedGameVersion();
                    gameStatus.setText((installed == null ? "설치되지 않음" : "설치됨: " + installed)
                            + "\n최신 한국어판: " + index.gameVersion);
                    gameAction.setEnabled(true);
                });
            } catch (Exception error) {
                latestGame = null;
                runOnUiThread(() -> gameAction.setEnabled(false));
                showError("게임 배포 정보를 불러오지 못했습니다.", error);
            }
        });
    }

    private void handleShizukuAction() {
        if (!isPackageInstalled(ShizukuRelease.PACKAGE_NAME)) {
            downloadLatestShizuku();
            return;
        }

        if (!Shizuku.pingBinder()) {
            openPackage(ShizukuRelease.PACKAGE_NAME);
            return;
        }

        if (Shizuku.checkSelfPermission() != PackageManager.PERMISSION_GRANTED) {
            if (Shizuku.shouldShowRequestPermissionRationale()) {
                detailStatus.setText("Shizuku 앱에서 Mobile Patcher 권한을 허용한 뒤 다시 시도하세요.");
                openPackage(ShizukuRelease.PACKAGE_NAME);
            } else {
                Shizuku.requestPermission(SHIZUKU_PERMISSION_REQUEST);
            }
            return;
        }

        openPackage(ShizukuRelease.PACKAGE_NAME);
    }

    private void downloadLatestShizuku() {
        setBusy(true, "최신 Shizuku 안정 버전을 확인하는 중입니다.");
        executor.execute(() -> {
            try {
                ShizukuRelease release = ShizukuRelease.parseLatest(HttpClient.getText(SHIZUKU_LATEST_API));
                File apk = downloadFile("shizuku-latest.apk");
                String actualHash = HttpClient.download(
                        apk,
                        release.downloadUrl,
                        release.size,
                        percent -> runOnUiThread(() -> progress.setProgress(percent)));
                if (!release.sha256.equals(actualHash)) {
                    apk.delete();
                    throw new IllegalStateException("GitHub asset digest와 APK SHA-256이 일치하지 않습니다.");
                }
                verifyApkPackage(apk, ShizukuRelease.PACKAGE_NAME, null);
                runOnUiThread(() -> {
                    setBusy(false, "Shizuku " + release.version + " 다운로드 및 검증 완료");
                    requestSystemInstall(apk);
                });
            } catch (Exception error) {
                runOnUiThread(() -> setBusy(false, null));
                showError("Shizuku 다운로드에 실패했습니다.", error);
            }
        });
    }

    private void downloadLatestGame() {
        DistributionIndex index = latestGame;
        if (index == null) {
            refreshStatus();
            return;
        }

        setBusy(true, "게임 APK를 다운로드하는 중입니다.");
        executor.execute(() -> {
            try {
                File apk = downloadFile("astral-party-latest.apk");
                String actualHash = HttpClient.download(
                        apk,
                        index.downloadUrl,
                        index.size,
                        percent -> runOnUiThread(() -> progress.setProgress(percent)));
                if (!index.sha256.equals(actualHash)) {
                    apk.delete();
                    throw new IllegalStateException("게임 APK SHA-256 검증에 실패했습니다.");
                }
                verifyApkPackage(apk, DistributionIndex.PACKAGE_NAME, index.gameVersion);
                runOnUiThread(() -> {
                    setBusy(false, "게임 APK 다운로드 및 검증 완료");
                    requestSystemInstall(apk);
                });
            } catch (Exception error) {
                runOnUiThread(() -> setBusy(false, null));
                showError("게임 APK 다운로드에 실패했습니다.", error);
            }
        });
    }

    private void requestSystemInstall(File apk) {
        if (!getPackageManager().canRequestPackageInstalls()) {
            pendingSystemInstall = apk;
            detailStatus.setText("이 앱의 '알 수 없는 앱 설치' 권한을 허용한 뒤 다시 시도하세요.");
            Intent intent = new Intent(
                    Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:" + getPackageName()));
            startActivity(intent);
            return;
        }

        openSystemInstaller(apk);
    }

    private void openSystemInstaller(File apk) {
        Uri uri = FileProvider.getUriForFile(this, getPackageName() + ".files", apk);
        Intent install = new Intent(Intent.ACTION_VIEW)
                .setDataAndType(uri, "application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(install);
    }

    private void verifyApkPackage(File apk, String expectedPackage, String expectedVersion) throws Exception {
        PackageInfo info = getPackageManager().getPackageArchiveInfo(apk.getAbsolutePath(), 0);
        if (info == null || !expectedPackage.equals(info.packageName)) {
            throw new IllegalStateException("다운로드한 APK packageName이 예상과 다릅니다.");
        }
        if (expectedVersion != null && !expectedVersion.equals(info.versionName)) {
            throw new IllegalStateException(
                    "다운로드한 APK 버전이 배포 정보와 다릅니다: " + info.versionName);
        }
    }

    private File downloadFile(String name) {
        return new File(new File(getCacheDir(), "downloads"), name);
    }

    private boolean isPackageInstalled(String packageName) {
        try {
            ApplicationInfo info = getPackageManager().getApplicationInfo(packageName, 0);
            return info.enabled;
        } catch (PackageManager.NameNotFoundException error) {
            return false;
        }
    }

    private String installedGameVersion() {
        try {
            PackageInfo info = getPackageManager().getPackageInfo(DistributionIndex.PACKAGE_NAME, 0);
            return info.versionName;
        } catch (PackageManager.NameNotFoundException error) {
            return null;
        }
    }

    private void launchGame() {
        Intent intent = getPackageManager().getLaunchIntentForPackage(DistributionIndex.PACKAGE_NAME);
        if (intent == null) {
            detailStatus.setText("게임 실행 Activity를 찾지 못했습니다.");
            return;
        }
        startActivity(intent);
    }

    private void openPackage(String packageName) {
        Intent intent = getPackageManager().getLaunchIntentForPackage(packageName);
        if (intent == null) {
            detailStatus.setText("앱을 열 수 없습니다: " + packageName);
            return;
        }
        startActivity(intent);
    }

    private void setBusy(boolean busy, String detail) {
        progress.setVisibility(busy ? View.VISIBLE : View.GONE);
        if (!busy) {
            progress.setProgress(0);
        }
        shizukuAction.setEnabled(!busy);
        gameAction.setEnabled(!busy && latestGame != null);
        refresh.setEnabled(!busy);
        if (detail != null) {
            detailStatus.setText(detail);
        }
    }

    private void showError(String prefix, Exception error) {
        runOnUiThread(() -> {
            String message = error.getMessage();
            detailStatus.setText(
                    message == null || message.trim().isEmpty() ? prefix : prefix + "\n" + message);
        });
    }
}
