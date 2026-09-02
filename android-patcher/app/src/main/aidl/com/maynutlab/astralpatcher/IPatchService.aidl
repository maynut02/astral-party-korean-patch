package com.maynutlab.astralpatcher;

import android.os.ParcelFileDescriptor;

interface IPatchService {
    String getServiceInfo() = 0;
    String inspectGame() = 1;
    void beginPatch(String transactionId, String catalogHash) = 2;
    String applyFile(
        String transactionId,
        in ParcelFileDescriptor payload,
        long size,
        String sha256,
        long sourceSize,
        String sourceSha256,
        String relativePath
    ) = 3;
    void commitPatch(
        String transactionId,
        String gameVersion,
        String catalogHash,
        String patchVersion
    ) = 4;
    void rollbackPatch(String transactionId) = 5;
    String restorePatch() = 6;
    String inspectPatchTargets(String requirementsJson) = 7;
    String getPatchDiagnostics(String transactionId) = 8;
    void stageOriginal(
        String transactionId,
        in ParcelFileDescriptor original,
        long sourceSize,
        String sourceSha256,
        String relativePath
    ) = 9;
    void beginGameInstall(String installId, int fileCount) = 10;
    void stageGameApk(
        String installId,
        String name,
        in ParcelFileDescriptor apk,
        long size,
        String sha256
    ) = 11;
    String commitGameInstall(String installId) = 12;
    void cancelGameInstall(String installId) = 13;
    void destroy() = 16777114;
}
