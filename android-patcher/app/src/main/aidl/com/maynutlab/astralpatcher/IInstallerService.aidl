package com.maynutlab.astralpatcher;

import android.os.ParcelFileDescriptor;

interface IInstallerService {
    String getServiceInfo() = 0;
    String installGameApk(in ParcelFileDescriptor apk, long size) = 1;
    void destroy() = 16777114;
}
