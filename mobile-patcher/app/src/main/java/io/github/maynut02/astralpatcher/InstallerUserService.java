package io.github.maynut02.astralpatcher;

import android.os.RemoteException;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;

public final class InstallerUserService extends IInstallerService.Stub {
    private static final int MAX_OUTPUT_BYTES = 64 * 1024;
    private static final String SERVICE_INFO = "AstralInstallerService/2";

    private Process installProcess;
    private OutputStream installInput;
    private ByteArrayOutputStream installOutput;
    private Thread installOutputReader;
    private long expectedInstallBytes;
    private long writtenInstallBytes;

    public InstallerUserService() {
    }

    @Override
    public String getServiceInfo() {
        return SERVICE_INFO + " uid=" + android.os.Process.myUid();
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
            installOutputReader.start();
        } catch (Exception error) {
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
            cancelInstallInternal();
            throw remoteException(error);
        }
    }

    @Override
    public synchronized String finishInstall() throws RemoteException {
        try {
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

            int exitCode = installProcess.waitFor();
            if (installOutputReader != null) {
                installOutputReader.join();
            }
            String result = new String(
                    installOutput.toByteArray(), StandardCharsets.UTF_8).trim();
            if (exitCode != 0 || !result.contains("Success")) {
                throw new IllegalStateException(
                        "pm install failed (exit=" + exitCode + "): " + result);
            }
            clearInstallState();
            return result;
        } catch (Exception error) {
            cancelInstallInternal();
            throw remoteException(error);
        }
    }

    @Override
    public synchronized void cancelInstall() {
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
}
