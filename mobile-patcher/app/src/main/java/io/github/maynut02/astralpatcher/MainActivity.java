package io.github.maynut02.astralpatcher;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.ApplicationInfo;
import android.content.pm.InstallSourceInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.os.IBinder;
import android.provider.Settings;
import android.text.format.DateFormat;
import android.util.Log;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import androidx.core.graphics.Insets;
import androidx.core.content.FileProvider;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;

import java.io.File;
import java.io.FileInputStream;
import java.util.Arrays;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import rikka.shizuku.Shizuku;

public final class MainActivity extends Activity {
    private static final String SHIZUKU_LATEST_API =
            "https://api.github.com/repos/thedjchi/Shizuku/releases/latest";
    private static final String GAME_INDEX_URL =
            "https://raw.githubusercontent.com/maynut02/astral-party-korean-patch/distribution/android-apk-index.json";
    private static final String PATCHER_INDEX_URL =
            "https://raw.githubusercontent.com/maynut02/astral-party-korean-patch/distribution/mobile-patcher-index.json";
    private static final int SHIZUKU_PERMISSION_REQUEST = 1001;
    private static final String INSTALLER_USER_SERVICE_TAG = "astral-game-installer";
    private static final String INSTALLER_SERVICE_DESCRIPTOR =
            "io.github.maynut02.astralpatcher.IInstallerService";
    private static final String INSTALLER_SERVICE_PROTOCOL = "AstralInstallerService/2";
    private static final int INSTALL_CHUNK_SIZE = 128 * 1024;
    private static final int MAX_LOG_CHARS = 24 * 1024;

    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    private TextView shizukuStatus;
    private TextView gameStatus;
    private TextView detailStatus;
    private LinearLayout patcherUpdatePanel;
    private TextView patcherUpdateStatus;
    private Button shizukuAction;
    private Button gameAction;
    private Button launchGame;
    private Button patcherUpdateAction;
    private Button refresh;
    private ProgressBar progress;
    private ProgressBar patcherUpdateProgress;
    private DistributionIndex latestGame;
    private MobilePatcherRelease latestPatcher;
    private File pendingSystemInstall;
    private File pendingGameInstall;
    private IInstallerService installerService;
    private Shizuku.UserServiceArgs installerServiceArgs;
    private boolean installerServiceBinding;
    private boolean busy;

    private final Shizuku.OnBinderReceivedListener binderReceivedListener = this::refreshStatus;
    private final Shizuku.OnBinderDeadListener binderDeadListener = this::refreshStatus;
    private final Shizuku.OnRequestPermissionResultListener permissionResultListener =
            (requestCode, grantResult) -> {
                if (requestCode == SHIZUKU_PERMISSION_REQUEST) {
                    refreshStatus();
                }
            };
    private final ServiceConnection installerServiceConnection = new ServiceConnection() {
        @Override
        public void onServiceConnected(ComponentName name, IBinder service) {
            try {
                String descriptor = service.getInterfaceDescriptor();
                appendLog("Shizuku 설치 서비스 Binder 확인: " + descriptor);
                if (!INSTALLER_SERVICE_DESCRIPTOR.equals(descriptor)) {
                    throw new IllegalStateException(
                            "예상하지 못한 Binder 인터페이스입니다: " + descriptor);
                }
            } catch (Exception error) {
                installerServiceBinding = false;
                pendingGameInstall = null;
                setBusy(false, null);
                showError("Shizuku 설치 서비스 연결에 실패했습니다.", error);
                releaseInstallerService();
                return;
            }

            installerService = IInstallerService.Stub.asInterface(service);
            installerServiceBinding = false;
            appendLog("Shizuku 설치 서비스에 연결되었습니다.");
            File apk = pendingGameInstall;
            pendingGameInstall = null;
            if (apk != null) {
                installGameWithBoundService(apk);
            }
        }

        @Override
        public void onServiceDisconnected(ComponentName name) {
            installerService = null;
            installerServiceBinding = false;
            appendLog("Shizuku 설치 서비스 연결이 종료되었습니다.");
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        setContentView(R.layout.activity_main);
        applySystemBarInsets();

        shizukuStatus = findViewById(R.id.shizukuStatus);
        gameStatus = findViewById(R.id.gameStatus);
        detailStatus = findViewById(R.id.detailStatus);
        patcherUpdatePanel = findViewById(R.id.patcherUpdatePanel);
        patcherUpdateStatus = findViewById(R.id.patcherUpdateStatus);
        shizukuAction = findViewById(R.id.shizukuAction);
        gameAction = findViewById(R.id.gameAction);
        launchGame = findViewById(R.id.launchGame);
        patcherUpdateAction = findViewById(R.id.patcherUpdateAction);
        refresh = findViewById(R.id.refresh);
        progress = findViewById(R.id.downloadProgress);
        patcherUpdateProgress = findViewById(R.id.patcherUpdateProgress);
        installerServiceArgs = new Shizuku.UserServiceArgs(
                new ComponentName(this, InstallerUserService.class))
                .daemon(false)
                .tag(INSTALLER_USER_SERVICE_TAG)
                .version(BuildConfig.VERSION_CODE)
                .processNameSuffix("installer");

        shizukuAction.setOnClickListener(view -> handleShizukuAction());
        gameAction.setOnClickListener(view -> downloadLatestGame());
        launchGame.setOnClickListener(view -> launchGame());
        patcherUpdateAction.setOnClickListener(view -> downloadLatestMobilePatcher());
        refresh.setOnClickListener(view -> refreshStatus());

        Shizuku.addBinderReceivedListenerSticky(binderReceivedListener);
        Shizuku.addBinderDeadListener(binderDeadListener);
        Shizuku.addRequestPermissionResultListener(permissionResultListener);
        refreshStatus();
    }

    private void applySystemBarInsets() {
        View root = findViewById(R.id.mainScroll);
        ViewCompat.setOnApplyWindowInsetsListener(root, (view, windowInsets) -> {
            Insets insets = windowInsets.getInsets(
                    WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
            view.setPadding(insets.left, insets.top, insets.right, insets.bottom);
            return windowInsets;
        });
        ViewCompat.requestApplyInsets(root);
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
        releaseInstallerService();
        executor.shutdownNow();
        super.onDestroy();
    }

    private void refreshStatus() {
        runOnUiThread(() -> {
            updateShizukuUi();
            updateInstalledGameUi();
            updateGameActionAvailability();
        });
        fetchLatestGameIndex();
        fetchLatestMobilePatcherIndex();
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
                    updateGameActionAvailability();
                });
            } catch (Exception error) {
                latestGame = null;
                runOnUiThread(() -> gameAction.setEnabled(false));
                showError("게임 배포 정보를 불러오지 못했습니다.", error);
            }
        });
    }

    private void fetchLatestMobilePatcherIndex() {
        executor.execute(() -> {
            try {
                MobilePatcherRelease release = MobilePatcherRelease.parse(
                        HttpClient.getText(PATCHER_INDEX_URL));
                latestPatcher = release;
                runOnUiThread(this::updatePatcherUpdateUi);
            } catch (Exception error) {
                latestPatcher = null;
                runOnUiThread(() -> patcherUpdatePanel.setVisibility(View.GONE));
                String message = error.getMessage();
                appendLog("Mobile Patcher 업데이트 확인 실패"
                        + (message == null || message.trim().isEmpty() ? "" : ": " + message.trim()));
            }
        });
    }

    private void updatePatcherUpdateUi() {
        MobilePatcherRelease release = latestPatcher;
        if (release == null || !release.isNewerThan(BuildConfig.VERSION_CODE)) {
            patcherUpdatePanel.setVisibility(View.GONE);
            return;
        }

        patcherUpdatePanel.setVisibility(View.VISIBLE);
        patcherUpdateStatus.setText(
                "현재 버전: " + BuildConfig.VERSION_NAME + "\n최신 버전: " + release.version);
        patcherUpdateAction.setEnabled(!busy);
    }

    private void downloadLatestMobilePatcher() {
        MobilePatcherRelease release = latestPatcher;
        if (release == null || !release.isNewerThan(BuildConfig.VERSION_CODE)) {
            fetchLatestMobilePatcherIndex();
            return;
        }

        setUpdateBusy(true, "Mobile Patcher " + release.version + " APK를 다운로드하는 중입니다.");
        appendLog("Mobile Patcher 업데이트 다운로드 시작: " + release.downloadUrl);
        executor.execute(() -> {
            try {
                File apk = downloadFile("astral-mobile-patcher-latest.apk");
                String actualHash = HttpClient.download(
                        apk,
                        release.downloadUrl,
                        release.size,
                        percent -> runOnUiThread(() -> patcherUpdateProgress.setProgress(percent)));
                if (!release.sha256.equals(actualHash)) {
                    apk.delete();
                    throw new IllegalStateException("Mobile Patcher APK SHA-256 검증에 실패했습니다.");
                }
                verifyMobilePatcherApk(apk, release);
                appendLog("Mobile Patcher " + release.version + " 다운로드 및 검증 완료");
                runOnUiThread(() -> {
                    setUpdateBusy(false, null);
                    requestSystemInstall(apk);
                });
            } catch (Exception error) {
                runOnUiThread(() -> setUpdateBusy(false, null));
                showError("Mobile Patcher 업데이트 다운로드에 실패했습니다.", error);
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
                appendLog("Shizuku 앱에서 Mobile Patcher 권한을 허용한 뒤 다시 시도하세요.");
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
        if (!isShizukuReady()) {
            appendLog("게임 설치에는 실행 중인 Shizuku와 Mobile Patcher 권한이 필요합니다.");
            updateGameActionAvailability();
            return;
        }

        DistributionIndex index = latestGame;
        if (index == null) {
            refreshStatus();
            return;
        }

        setBusy(true, "게임 APK를 다운로드하는 중입니다.");
        appendLog("게임 APK 다운로드 시작: " + index.downloadUrl);
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
                appendLog("게임 APK 다운로드 및 SHA-256 검증 완료: " + apk.length() + " bytes");
                verifyApkPackage(apk, DistributionIndex.PACKAGE_NAME, index.gameVersion);
                appendLog("게임 APK 패키지 검증 완료: " + DistributionIndex.PACKAGE_NAME
                        + " / " + index.gameVersion);
                runOnUiThread(() -> installGameWithShizuku(apk));
            } catch (Exception error) {
                runOnUiThread(() -> setBusy(false, null));
                showError("게임 APK 다운로드에 실패했습니다.", error);
            }
        });
    }

    private void installGameWithShizuku(File apk) {
        if (!isShizukuReady()) {
            setBusy(false, "Shizuku 연결 또는 권한이 해제되어 게임을 설치할 수 없습니다.");
            updateGameActionAvailability();
            return;
        }

        if (installerService != null) {
            installGameWithBoundService(apk);
            return;
        }

        if (installerServiceBinding) {
            pendingGameInstall = apk;
            return;
        }

        pendingGameInstall = apk;
        installerServiceBinding = true;
        appendLog("Shizuku 설치 서비스에 연결하는 중입니다.");
        try {
            Shizuku.bindUserService(installerServiceArgs, installerServiceConnection);
        } catch (RuntimeException error) {
            installerServiceBinding = false;
            pendingGameInstall = null;
            setBusy(false, null);
            showError("Shizuku 설치 서비스 연결에 실패했습니다.", error);
        }
    }

    private void installGameWithBoundService(File apk) {
        IInstallerService service = installerService;
        if (service == null) {
            setBusy(false, "Shizuku 설치 서비스가 연결되지 않았습니다.");
            return;
        }

        appendLog("Shizuku를 통해 게임을 설치하는 중입니다. APK 크기: " + apk.length() + " bytes");
        executor.execute(() -> {
            boolean installStarted = false;
            try {
                String serviceInfo = service.getServiceInfo();
                appendLog("Shizuku 설치 서비스 확인: " + serviceInfo);
                if (serviceInfo == null || !serviceInfo.startsWith(INSTALLER_SERVICE_PROTOCOL)) {
                    throw new IllegalStateException(
                            "설치 서비스 프로토콜 확인에 실패했습니다: " + serviceInfo);
                }

                service.beginInstall(apk.length());
                installStarted = true;
                byte[] buffer = new byte[INSTALL_CHUNK_SIZE];
                try (FileInputStream input = new FileInputStream(apk)) {
                    int read;
                    while ((read = input.read(buffer)) != -1) {
                        byte[] chunk = read == buffer.length
                                ? buffer
                                : Arrays.copyOf(buffer, read);
                        service.writeInstallChunk(chunk);
                    }
                }

                String installResult = service.finishInstall();
                installStarted = false;
                appendLog("pm install 결과: " + installResult);
                if (installResult == null || installResult.trim().isEmpty()) {
                    throw new IllegalStateException("pm install이 빈 결과를 반환했습니다.");
                }
                String installer = installedGameInstaller();
                appendLog("설치 출처 확인 결과: " + installer);
                if (!DistributionIndex.INSTALLER_PACKAGE.equals(installer)) {
                    throw new IllegalStateException(
                            "설치 출처 검증에 실패했습니다: " + installer);
                }
                runOnUiThread(() -> {
                    setBusy(false, "게임 설치 완료 · 설치 출처: Google Play");
                    refreshStatus();
                });
            } catch (Exception error) {
                if (installStarted) {
                    try {
                        service.cancelInstall();
                    } catch (Exception cancelError) {
                        appendLog("Shizuku 설치 작업 취소 중 오류: " + cancelError.getMessage());
                    }
                }
                runOnUiThread(() -> setBusy(false, null));
                showError("Shizuku 게임 설치에 실패했습니다.", error);
            } finally {
                runOnUiThread(this::releaseInstallerService);
            }
        });
    }

    private void releaseInstallerService() {
        installerService = null;
        installerServiceBinding = false;
        pendingGameInstall = null;
        if (installerServiceArgs == null || !Shizuku.pingBinder()) {
            return;
        }
        try {
            Shizuku.unbindUserService(installerServiceArgs, installerServiceConnection, true);
        } catch (RuntimeException ignored) {
            // Shizuku may stop while the Activity is being destroyed.
        }
    }

    private void requestSystemInstall(File apk) {
        if (!getPackageManager().canRequestPackageInstalls()) {
            pendingSystemInstall = apk;
            appendLog("이 앱의 '알 수 없는 앱 설치' 권한을 허용한 뒤 다시 시도하세요.");
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

    private void verifyMobilePatcherApk(File apk, MobilePatcherRelease release) throws Exception {
        PackageInfo info = getPackageManager().getPackageArchiveInfo(
                apk.getAbsolutePath(), PackageManager.GET_SIGNING_CERTIFICATES);
        if (info == null || !getPackageName().equals(info.packageName)) {
            throw new IllegalStateException("다운로드한 Mobile Patcher APK packageName이 예상과 다릅니다.");
        }
        if (!release.version.equals(info.versionName)) {
            throw new IllegalStateException(
                    "다운로드한 Mobile Patcher APK 버전이 배포 정보와 다릅니다: " + info.versionName);
        }
        if (info.getLongVersionCode() != release.versionCode) {
            throw new IllegalStateException(
                    "다운로드한 Mobile Patcher APK versionCode가 배포 정보와 다릅니다: "
                            + info.getLongVersionCode());
        }
        if (info.getLongVersionCode() <= BuildConfig.VERSION_CODE) {
            throw new IllegalStateException("현재 버전보다 새로운 Mobile Patcher APK가 아닙니다.");
        }

        PackageInfo current = getPackageManager().getPackageInfo(
                getPackageName(), PackageManager.GET_SIGNING_CERTIFICATES);
        if (!hasSameCurrentSigner(current, info)) {
            throw new IllegalStateException("다운로드한 Mobile Patcher APK 서명이 현재 앱과 다릅니다.");
        }
    }

    private static boolean hasSameCurrentSigner(PackageInfo first, PackageInfo second) {
        if (first.signingInfo == null || second.signingInfo == null) {
            return false;
        }
        android.content.pm.Signature[] firstSigners = first.signingInfo.getApkContentsSigners();
        android.content.pm.Signature[] secondSigners = second.signingInfo.getApkContentsSigners();
        if (firstSigners.length != secondSigners.length) {
            return false;
        }
        for (android.content.pm.Signature signer : firstSigners) {
            boolean matched = false;
            for (android.content.pm.Signature candidate : secondSigners) {
                if (signer.equals(candidate)) {
                    matched = true;
                    break;
                }
            }
            if (!matched) {
                return false;
            }
        }
        return true;
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

    private String installedGameInstaller() throws PackageManager.NameNotFoundException {
        InstallSourceInfo source = getPackageManager().getInstallSourceInfo(DistributionIndex.PACKAGE_NAME);
        return source.getInstallingPackageName();
    }

    private boolean isShizukuReady() {
        return Shizuku.pingBinder()
                && Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED;
    }

    private void updateGameActionAvailability() {
        gameAction.setEnabled(!busy && latestGame != null && isShizukuReady());
    }

    private void launchGame() {
        Intent intent = getPackageManager().getLaunchIntentForPackage(DistributionIndex.PACKAGE_NAME);
        if (intent == null) {
            appendLog("게임 실행 Activity를 찾지 못했습니다.");
            return;
        }
        startActivity(intent);
    }

    private void openPackage(String packageName) {
        Intent intent = getPackageManager().getLaunchIntentForPackage(packageName);
        if (intent == null) {
            appendLog("앱을 열 수 없습니다: " + packageName);
            return;
        }
        startActivity(intent);
    }

    private void setBusy(boolean busy, String detail) {
        this.busy = busy;
        progress.setVisibility(busy ? View.VISIBLE : View.GONE);
        patcherUpdateProgress.setVisibility(View.GONE);
        if (!busy) {
            progress.setProgress(0);
        }
        updateActionAvailability();
        if (detail != null) {
            appendLog(detail);
        }
    }

    private void setUpdateBusy(boolean busy, String detail) {
        this.busy = busy;
        progress.setVisibility(View.GONE);
        patcherUpdateProgress.setVisibility(busy ? View.VISIBLE : View.GONE);
        if (!busy) {
            patcherUpdateProgress.setProgress(0);
        }
        updateActionAvailability();
        if (detail != null) {
            appendLog(detail);
        }
    }

    private void updateActionAvailability() {
        shizukuAction.setEnabled(!busy);
        gameAction.setEnabled(!busy && latestGame != null && isShizukuReady());
        refresh.setEnabled(!busy);
        MobilePatcherRelease release = latestPatcher;
        patcherUpdateAction.setEnabled(!busy
                && release != null
                && release.isNewerThan(BuildConfig.VERSION_CODE));
    }

    private void showError(String prefix, Exception error) {
        String message = error.getMessage();
        StringBuilder details = new StringBuilder(prefix)
                .append('\n')
                .append(error.getClass().getName());
        if (message != null && !message.trim().isEmpty()) {
            details.append(": ").append(message.trim());
        }
        String stackTrace = Log.getStackTraceString(error);
        if (stackTrace != null && !stackTrace.trim().isEmpty()) {
            details.append('\n').append(stackTrace.trim());
        }
        appendLog(details.toString());
    }

    private void appendLog(String message) {
        if (message == null || message.trim().isEmpty()) {
            return;
        }
        runOnUiThread(() -> {
            String timestamp = DateFormat.format("HH:mm:ss", System.currentTimeMillis()).toString();
            String existing = detailStatus.getText().toString();
            if (getString(R.string.log_empty).equals(existing)) {
                existing = "";
            }
            String next = existing
                    + (existing.isEmpty() ? "" : "\n\n")
                    + "[" + timestamp + "] " + message.trim();
            if (next.length() > MAX_LOG_CHARS) {
                next = next.substring(next.length() - MAX_LOG_CHARS);
            }
            detailStatus.setText(next);
        });
    }
}
