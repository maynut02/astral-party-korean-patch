package com.maynutlab.astralpatcher;

import android.os.ParcelFileDescriptor;

interface IPatchService {
    String getServiceInfo() = 0;
    String inspectGame() = 1;
    void beginPatch(String transactionId, String catalogHash) = 2;
    void applyFile(
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
    void destroy() = 16777114;
}
