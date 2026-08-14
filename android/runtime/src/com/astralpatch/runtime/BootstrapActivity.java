package com.astralpatch.runtime;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

public final class BootstrapActivity extends Activity {
    private TextView status;
    private RuntimeConfig config;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        showBootstrapUi();
        try {
            config = RuntimeConfig.load(this);
        } catch (Exception error) {
            status.setText("한글패치 설정을 읽지 못했습니다.\n" + error.getMessage());
            return;
        }

        Thread worker = new Thread(new Runnable() {
            @Override
            public void run() {
                runBootstrap();
            }
        }, "AstralPatchBootstrap");
        worker.start();
    }

    private void runBootstrap() {
        String catalogHash = null;
        boolean watchCurrentCatalog = true;
        try {
            PatchEngine.Result result = PatchEngine.prepare(
                    getApplicationContext(),
                    config,
                    new PatchEngine.Progress() {
                        @Override
                        public void update(final String message) {
                            updateStatus(message);
                        }
                    });
            catalogHash = result.catalogHash;
            switch (result.status) {
                case PATCHED:
                    updateStatus("한글패치 적용이 완료되었습니다.");
                    watchCurrentCatalog = false;
                    clearRestartMarker();
                    break;
                case ALREADY_PATCHED:
                    updateStatus("한글패치가 최신 상태입니다.");
                    watchCurrentCatalog = false;
                    clearRestartMarker();
                    break;
                case WAITING_FOR_GAME_DOWNLOAD:
                    updateStatus("게임 리소스 다운로드가 필요합니다. 게임을 시작합니다.");
                    watchCurrentCatalog = true;
                    break;
                case NO_PATCH:
                    updateStatus("현재 리소스용 한글패치가 아직 없습니다. 게임을 시작합니다.");
                    watchCurrentCatalog = true;
                    break;
                case NO_CATALOG:
                default:
                    updateStatus("첫 리소스 다운로드를 위해 게임을 시작합니다.");
                    watchCurrentCatalog = true;
                    break;
            }
        } catch (final Exception error) {
            catalogHash = PatchEngine.currentCatalogHash(getApplicationContext(), config);
            watchCurrentCatalog = true;
            new Handler(Looper.getMainLooper()).post(new Runnable() {
                @Override
                public void run() {
                    Toast.makeText(
                            BootstrapActivity.this,
                            "한글패치 확인에 실패했습니다. 원본 상태로 게임을 시작합니다: "
                                    + safeMessage(error),
                            Toast.LENGTH_LONG)
                            .show();
                }
            });
        }

        UpdateWatcher.start(
                getApplicationContext(), config, catalogHash, watchCurrentCatalog);
        final Handler main = new Handler(Looper.getMainLooper());
        main.postDelayed(new Runnable() {
            @Override
            public void run() {
                launchGame();
            }
        }, 250L);
    }

    private void launchGame() {
        try {
            Intent intent = new Intent();
            intent.setClassName(getPackageName(), config.gameActivity);
            intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP);
            startActivity(intent);
            finish();
        } catch (Exception error) {
            status.setText("게임 Activity를 시작하지 못했습니다.\n" + safeMessage(error));
        }
    }

    private void clearRestartMarker() {
        getSharedPreferences("astralpatch", MODE_PRIVATE)
                .edit()
                .remove("restartRequired")
                .remove("restartCatalogHash")
                .apply();
    }

    private void updateStatus(final String message) {
        new Handler(Looper.getMainLooper()).post(new Runnable() {
            @Override
            public void run() {
                status.setText(message);
            }
        });
    }

    private void showBootstrapUi() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setGravity(Gravity.CENTER);
        layout.setPadding(64, 64, 64, 64);
        layout.setBackgroundColor(Color.BLACK);

        ProgressBar progress = new ProgressBar(this);
        LinearLayout.LayoutParams progressParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        progressParams.bottomMargin = 36;
        layout.addView(progress, progressParams);

        status = new TextView(this);
        status.setText("한글패치 상태를 확인하는 중입니다...");
        status.setTextColor(Color.WHITE);
        status.setTextSize(16f);
        status.setGravity(Gravity.CENTER);
        layout.addView(status, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        setContentView(layout);
    }

    private static String safeMessage(Throwable error) {
        String message = error.getMessage();
        return message == null || message.trim().isEmpty()
                ? error.getClass().getSimpleName()
                : message;
    }
}
