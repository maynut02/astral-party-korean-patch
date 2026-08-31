package com.maynutlab.astralpatcher;

import android.os.ParcelFileDescriptor;
import android.os.RemoteException;
import android.system.Os;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;

public final class InstallerUserService extends IInstallerService.Stub {
    private static final int BUFFER_SIZE = 128 * 1024;
    private static final int MAX_OUTPUT_BYTES = 64 * 1024;
    private static final int MAX_STATUS_OUTPUT_CHARS = 512;
    private static final long INSTALL_TIMEOUT_SECONDS = 180;
    private static final long OUTPUT_READER_JOIN_TIMEOUT_MS = 5_000;
    private static final File INSTALL_STAGING_DIR = new File("/data/local/tmp");
    private static final String SERVICE_INFO = "AstralInstallerService/5";

    private boolean installing;
    private String lastInstallStatus = "idle";

    @Override
    public synchronized String getServiceInfo() {
        return SERVICE_INFO
                + " uid=" + android.os.Process.myUid()
                + " pid=" + android.os.Process.myPid()
                + " state=" + lastInstallStatus;
    }

    @Override
    public String installGameApk(ParcelFileDescriptor apk, long size) throws RemoteException {
        synchronized (this) {
            if (installing) {
                throw remoteException(new IllegalStateException("An installation is already in progress."));
            }
            if (apk == null) {
                throw remoteException(new IllegalArgumentException("APK file descriptor is missing."));
            }
            if (size <= 0) {
                throw remoteException(new IllegalArgumentException("APK size must be positive."));
            }
            installing = true;
            lastInstallStatus = "starting expected=" + size;
        }

        Process process = null;
        Thread outputReader = null;
        ByteArrayOutputStream commandOutput = new ByteArrayOutputStream();
        File stagedApk = null;
        long written = 0;
        try {
            stagedApk = File.createTempFile("astral-patcher-", ".apk", INSTALL_STAGING_DIR);
            try (ParcelFileDescriptor.AutoCloseInputStream input =
                         new ParcelFileDescriptor.AutoCloseInputStream(apk);
                 FileOutputStream output = new FileOutputStream(stagedApk)) {
                byte[] buffer = new byte[BUFFER_SIZE];
                int read;
                while ((read = input.read(buffer)) != -1) {
                    output.write(buffer, 0, read);
                    written += read;
                    if (written > size) {
                        throw new IllegalStateException(
                                "APK stream exceeds expected size: expected " + size
                                        + ", wrote at least " + written);
                    }
                }
                output.flush();
            }

            if (written != size) {
                throw new IllegalStateException(
                        "APK stream size mismatch: expected " + size + ", wrote " + written);
            }

            // PackageManager runs outside the shell process, so the staged APK must be readable.
            Os.chmod(stagedApk.getAbsolutePath(), 0644);
            synchronized (this) {
                lastInstallStatus = "staged written=" + written;
            }

            process = new ProcessBuilder(
                    "/system/bin/pm",
                    "install",
                    "-r",
                    "-i",
                    DistributionIndex.INSTALLER_PACKAGE,
                    stagedApk.getAbsolutePath())
                    .redirectErrorStream(true)
                    .start();

            Process runningProcess = process;
            outputReader = new Thread(
                    () -> readCommandOutput(runningProcess.getInputStream(), commandOutput),
                    "pm-install-output");
            outputReader.start();

            synchronized (this) {
                lastInstallStatus = "installing written=" + written;
            }

            if (!process.waitFor(INSTALL_TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
                process.destroyForcibly();
                throw new IllegalStateException(
                        "pm install timed out after " + INSTALL_TIMEOUT_SECONDS + " seconds.");
            }
            if (outputReader != null) {
                outputReader.join(OUTPUT_READER_JOIN_TIMEOUT_MS);
            }

            int exitCode = process.exitValue();
            String result = new String(commandOutput.toByteArray(), StandardCharsets.UTF_8).trim();
            if (exitCode != 0) {
                throw new IllegalStateException(
                        "pm install failed (exit=" + exitCode + "): " + statusOutput(result));
            }

            String normalizedResult = result.isEmpty() ? "Success (exit=0)" : result;
            synchronized (this) {
                lastInstallStatus = "success written=" + written
                        + " exit=" + exitCode
                        + " output=" + statusOutput(normalizedResult);
            }
            return normalizedResult;
        } catch (Exception error) {
            if (error instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            if (process != null && process.isAlive()) {
                process.destroyForcibly();
            }
            if (outputReader != null && outputReader.isAlive()) {
                try {
                    outputReader.join(OUTPUT_READER_JOIN_TIMEOUT_MS);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                }
            }
            String processStatus = processStatus(process, commandOutput);
            synchronized (this) {
                lastInstallStatus = "failed written=" + written
                        + " " + failureStatus(error)
                        + " " + processStatus;
            }
            throw remoteException(error);
        } finally {
            if (outputReader != null && outputReader.isAlive()) {
                try {
                    outputReader.join(OUTPUT_READER_JOIN_TIMEOUT_MS);
                } catch (InterruptedException error) {
                    Thread.currentThread().interrupt();
                }
            }
            if (stagedApk != null && stagedApk.exists()) {
                stagedApk.delete();
            }
            synchronized (this) {
                installing = false;
            }
        }
    }

    private static void readCommandOutput(InputStream input, ByteArrayOutputStream output) {
        byte[] buffer = new byte[4096];
        try (InputStream stream = input) {
            int read;
            while ((read = stream.read(buffer)) != -1) {
                int remaining = MAX_OUTPUT_BYTES - output.size();
                if (remaining > 0) {
                    output.write(buffer, 0, Math.min(read, remaining));
                }
            }
        } catch (Exception ignored) {
            // The install result is validated from the process exit code and captured output.
        }
    }

    @Override
    public synchronized void destroy() {
        lastInstallStatus = "destroyed";
        System.exit(0);
    }

    private static RemoteException remoteException(Exception error) {
        String message = error.getMessage();
        return new RemoteException(message == null ? error.getClass().getSimpleName() : message);
    }

    private static String failureStatus(Exception error) {
        String message = error.getMessage();
        return error.getClass().getSimpleName() + ": "
                + statusOutput(message == null ? "<no message>" : message);
    }

    private static String processStatus(Process process, ByteArrayOutputStream output) {
        String exit = "running";
        if (process == null) {
            exit = "not-started";
        } else if (!process.isAlive()) {
            try {
                exit = Integer.toString(process.exitValue());
            } catch (IllegalThreadStateException ignored) {
                exit = "running";
            }
        }
        String result = new String(output.toByteArray(), StandardCharsets.UTF_8).trim();
        return "pmExit=" + exit + " pmOutput=" + statusOutput(result);
    }

    private static String statusOutput(String value) {
        if (value == null || value.isEmpty()) {
            return "<empty>";
        }
        String normalized = value.replace('\n', ' ').replace('\r', ' ');
        if (normalized.length() <= MAX_STATUS_OUTPUT_CHARS) {
            return normalized;
        }
        return normalized.substring(0, MAX_STATUS_OUTPUT_CHARS) + "...";
    }
}
