package io.github.maynut02.astralpatcher;

import android.os.RemoteException;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;

public final class InstallerUserService extends IInstallerService.Stub {
    private static final int MAX_OUTPUT_BYTES = 64 * 1024;
    private static final int MAX_STATUS_OUTPUT_CHARS = 512;
    private static final long INSTALL_TIMEOUT_SECONDS = 180;
    private static final long OUTPUT_READER_JOIN_TIMEOUT_MS = 5_000;
    private static final String SERVICE_INFO = "AstralInstallerService/3";

    private Process installProcess;
    private OutputStream installInput;
    private ByteArrayOutputStream installOutput;
    private Thread installOutputReader;
    private long expectedInstallBytes;
    private long writtenInstallBytes;
    private String lastInstallStatus = "idle";

    public InstallerUserService() {
    }

    @Override
    public synchronized String getServiceInfo() {
        String state;
        if (installProcess != null) {
            state = "streaming expected=" + expectedInstallBytes + " written=" + writtenInstallBytes;
        } else {
            state = lastInstallStatus;
        }
        return SERVICE_INFO + " uid=" + android.os.Process.myUid() + " state=" + state;
    }

    @Override
    public synchronized void beginInstall(long size) throws RemoteException {
        try {
            if (size <= 0) {
                throw new IllegalArgumentException("APK size must be positive.");
            }
            if (installProcess != null) {
                throw new IllegalStateException("An installation is already in progress.");
            }

            Process process = new ProcessBuilder(
                    "/system/bin/pm",
                    "install",
                    "-r",
                    "-i",
                    DistributionIndex.INSTALLER_PACKAGE,
                    "--pkg",
                    DistributionIndex.PACKAGE_NAME,
                    "-S",
                    Long.toString(size),
                    "-")
                    .redirectErrorStream(true)
                    .start();

            OutputStream processInput = process.getOutputStream();
            ByteArrayOutputStream commandOutput = new ByteArrayOutputStream();
            Thread outputReader = new Thread(
                    () -> readCommandOutput(process.getInputStream(), commandOutput),
                    "pm-install-output");

            installProcess = process;
            installInput = processInput;
            installOutput = commandOutput;
            expectedInstallBytes = size;
            writtenInstallBytes = 0;
            installOutputReader = outputReader;
            lastInstallStatus = "starting expected=" + size;
            installOutputReader.start();
        } catch (Exception error) {
            lastInstallStatus = failureStatus(error);
            cancelInstallInternal();
            throw remoteException(error);
        }
    }

    @Override
    public synchronized void writeInstallChunk(byte[] data) throws RemoteException {
        try {
            if (installProcess == null || installInput == null) {
                throw new IllegalStateException("No installation is in progress.");
            }
            if (data == null || data.length == 0) {
                throw new IllegalArgumentException("Install chunk is empty.");
            }
            long nextWritten = writtenInstallBytes + data.length;
            if (nextWritten > expectedInstallBytes) {
                throw new IllegalStateException(
                        "APK stream exceeds expected size: expected " + expectedInstallBytes
                                + ", attempted " + nextWritten);
            }

            installInput.write(data);
            writtenInstallBytes = nextWritten;
        } catch (Exception error) {
            lastInstallStatus = failureStatus(error);
            cancelInstallInternal();
            throw remoteException(error);
        }
    }

    @Override
    public String finishInstall() throws RemoteException {
        finishInstallV3();
        return getServiceInfo();
    }

    @Override
    public void finishInstallV3() throws RemoteException {
        Process process = null;
        Thread outputReader = null;
        ByteArrayOutputStream commandOutput = null;
        try {
            synchronized (this) {
                if (installProcess == null || installInput == null) {
                    throw new IllegalStateException("No installation is in progress.");
                }
                if (writtenInstallBytes != expectedInstallBytes) {
                    throw new IllegalStateException(
                            "APK stream size mismatch: expected " + expectedInstallBytes
                                    + ", wrote " + writtenInstallBytes);
                }

                installInput.flush();
                installInput.close();
                installInput = null;
                lastInstallStatus = "finishing expected=" + expectedInstallBytes
                        + " written=" + writtenInstallBytes;

                process = installProcess;
                outputReader = installOutputReader;
                commandOutput = installOutput;
            }

            if (!process.waitFor(INSTALL_TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
                process.destroy();
                throw new IllegalStateException(
                        "pm install timed out after " + INSTALL_TIMEOUT_SECONDS + " seconds.");
            }
            if (outputReader != null) {
                outputReader.join(OUTPUT_READER_JOIN_TIMEOUT_MS);
            }

            int exitCode = process.exitValue();
            String result = new String(
                    commandOutput.toByteArray(), StandardCharsets.UTF_8).trim();
            if (exitCode != 0) {
                throw new IllegalStateException(
                        "pm install failed (exit=" + exitCode + "): " + result);
            }

            synchronized (this) {
                if (installProcess != process) {
                    throw new IllegalStateException("Installation state changed before completion.");
                }
                lastInstallStatus = "success exit=" + exitCode + " output=" + statusOutput(result);
                clearInstallState();
            }
        } catch (Exception error) {
            if (error instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            synchronized (this) {
                lastInstallStatus = failureStatus(error);
                if (installProcess == process || process == null) {
                    cancelInstallInternal();
                }
            }
            throw remoteException(error);
        }
    }

    @Override
    public synchronized void cancelInstall() {
        if (installProcess != null) {
            lastInstallStatus = "cancelled expected=" + expectedInstallBytes
                    + " written=" + writtenInstallBytes;
        }
        cancelInstallInternal();
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
        cancelInstallInternal();
        System.exit(0);
    }

    private void cancelInstallInternal() {
        if (installInput != null) {
            try {
                installInput.close();
            } catch (Exception ignored) {
            }
        }
        if (installProcess != null) {
            installProcess.destroy();
        }
        clearInstallState();
    }

    private void clearInstallState() {
        installProcess = null;
        installInput = null;
        installOutput = null;
        installOutputReader = null;
        expectedInstallBytes = 0;
        writtenInstallBytes = 0;
    }

    private static RemoteException remoteException(Exception error) {
        String message = error.getMessage();
        return new RemoteException(message == null ? error.getClass().getSimpleName() : message);
    }

    private static String failureStatus(Exception error) {
        String message = error.getMessage();
        return "failed " + error.getClass().getSimpleName() + ": "
                + statusOutput(message == null ? "<no message>" : message);
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
