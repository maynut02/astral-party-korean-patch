use std::collections::BTreeMap;
use std::ffi::OsString;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use reqwest::blocking::Client;
use reqwest::header::CACHE_CONTROL;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use thiserror::Error;

pub const ANDROID_PACKAGE: &str = "com.feimo.astralpartyjpn";
pub const PLAY_INSTALLER_PACKAGE: &str = "com.android.vending";
const INDEX_SCHEMA_VERSION: u32 = 1;
const MAX_INDEX_BYTES: u64 = 64 * 1024;
#[cfg(windows)]
const MAX_PLATFORM_TOOLS_BYTES: u64 = 128 * 1024 * 1024;
#[cfg(windows)]
const PLATFORM_TOOLS_URL: &str =
    "https://dl.google.com/android/repository/platform-tools-latest-windows.zip";
const COMMAND_TIMEOUT: Duration = Duration::from_secs(20);
const INSTALL_TIMEOUT: Duration = Duration::from_secs(180);

#[derive(Debug, Error)]
pub enum AndroidError {
    #[error("HTTP error: {0}")]
    Http(#[from] reqwest::Error),
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("unsupported Android APK index schema: {0}")]
    UnsupportedIndexSchema(u32),
    #[error("invalid Android APK index: {0}")]
    InvalidIndex(String),
    #[error("Android 연결 도구를 준비하지 못했습니다: {0}")]
    PlatformTools(String),
    #[error(
        "연결된 Android 기기를 찾지 못했습니다. USB 연결/USB 디버깅을 확인하고, Windows에서 기기가 ADB 인터페이스로 인식되지 않으면 제조사 USB 드라이버를 확인하세요."
    )]
    NoDevice,
    #[error("USB 디버깅 승인이 필요합니다: {0}. 기기 화면의 RSA 허용 창을 승인하세요.")]
    UnauthorizedDevice(String),
    #[error(
        "Android 기기가 offline 상태입니다: {0}. USB를 다시 연결하거나 앱플레이어를 재시작하세요."
    )]
    OfflineDevice(String),
    #[error("패치 가능한 기기가 여러 대 감지되었습니다: {0}")]
    MultipleDevices(String),
    #[error("ADB 명령이 실패했습니다: {0}")]
    Adb(String),
    #[error("APK 다운로드 크기가 올바르지 않습니다: expected {expected}, actual {actual}")]
    ApkSizeMismatch { expected: u64, actual: u64 },
    #[error("APK SHA-256이 올바르지 않습니다")]
    ApkHashMismatch,
    #[error("설치 후 installer가 {expected}가 아닙니다: {actual}")]
    InstallerMismatch { expected: String, actual: String },
    #[error("설치 후 게임 버전이 {expected}가 아닙니다: {actual}")]
    InstalledVersionMismatch { expected: String, actual: String },
    #[error("Android 자동 설치는 Windows에서만 지원됩니다")]
    UnsupportedPlatform,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AndroidApkIndex {
    pub schema_version: u32,
    pub package_name: String,
    pub game_version: String,
    pub download_url: String,
    pub sha256: String,
    pub size: u64,
    pub installer_package_name: String,
}

impl AndroidApkIndex {
    pub fn validate(&self, release_base_url: &str) -> Result<(), AndroidError> {
        if self.schema_version != INDEX_SCHEMA_VERSION {
            return Err(AndroidError::UnsupportedIndexSchema(self.schema_version));
        }
        if self.package_name != ANDROID_PACKAGE {
            return Err(AndroidError::InvalidIndex(format!(
                "unexpected package {}",
                self.package_name
            )));
        }
        if self.game_version.trim().is_empty() {
            return Err(AndroidError::InvalidIndex("empty game version".into()));
        }
        if self.installer_package_name != PLAY_INSTALLER_PACKAGE {
            return Err(AndroidError::InvalidIndex(format!(
                "unexpected installer {}",
                self.installer_package_name
            )));
        }
        if self.size == 0 {
            return Err(AndroidError::InvalidIndex("APK size is zero".into()));
        }
        if !is_lower_hex_64(&self.sha256) {
            return Err(AndroidError::InvalidIndex("invalid APK SHA-256".into()));
        }
        let prefix = format!("{}/", release_base_url.trim_end_matches('/'));
        if !self.download_url.starts_with(&prefix)
            || !self.download_url.ends_with("/AstralParty_INT_Korean.apk")
        {
            return Err(AndroidError::InvalidIndex(format!(
                "unexpected APK URL {}",
                self.download_url
            )));
        }
        Ok(())
    }
}

fn is_lower_hex_64(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AndroidDeviceState {
    Device,
    Unauthorized,
    Offline,
    Other,
}

impl AndroidDeviceState {
    fn parse(value: &str) -> Self {
        match value {
            "device" => Self::Device,
            "unauthorized" => Self::Unauthorized,
            "offline" => Self::Offline,
            _ => Self::Other,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AndroidDeviceKind {
    Physical,
    Emulator,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AndroidDevice {
    pub serial: String,
    pub state: AndroidDeviceState,
    pub model: String,
    pub provider: String,
    pub kind: AndroidDeviceKind,
    pub adb_path: PathBuf,
}

impl AndroidDevice {
    pub fn display_name(&self) -> String {
        let model = if self.model.trim().is_empty() {
            self.serial.as_str()
        } else {
            self.model.as_str()
        };
        format!("{model} · {}", self.provider)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AndroidPackageInfo {
    pub version_name: Option<String>,
    pub installer_package_name: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AndroidInstallOutcome {
    Installed {
        device: AndroidDevice,
        game_version: String,
    },
    NeedsReinstall {
        device: AndroidDevice,
        game_version: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AndroidProgress {
    PreparingAdb,
    DiscoveringDevices,
    DeviceSelected { name: String },
    ResolvingApk,
    DownloadingApk { current: u64, total: u64 },
    Installing,
    RemovingOfficialInstall,
    Verifying,
}

#[derive(Debug, Clone)]
pub struct AndroidService {
    state_root: PathBuf,
    index_url: String,
    release_base_url: String,
}

impl AndroidService {
    pub fn new(
        state_root: impl Into<PathBuf>,
        index_url: impl Into<String>,
        release_base_url: impl Into<String>,
    ) -> Self {
        Self {
            state_root: state_root.into(),
            index_url: index_url.into(),
            release_base_url: release_base_url.into(),
        }
    }

    pub fn install_latest_with_progress<F>(
        &self,
        force_reinstall: bool,
        mut progress: F,
    ) -> Result<AndroidInstallOutcome, AndroidError>
    where
        F: FnMut(AndroidProgress),
    {
        progress(AndroidProgress::PreparingAdb);
        let (primary_adb, platform_tools_error) = match ensure_platform_tools(&self.state_root) {
            Ok(adb) => (Some(adb), None),
            Err(error) => (None, Some(error)),
        };
        progress(AndroidProgress::DiscoveringDevices);
        let devices = discover_devices(primary_adb.as_deref())?;
        if devices.is_empty()
            && let Some(error) = platform_tools_error
        {
            return Err(error);
        }
        let device = select_target_device(&devices)?;
        progress(AndroidProgress::DeviceSelected {
            name: device.display_name(),
        });

        progress(AndroidProgress::ResolvingApk);
        let client = http_client()?;
        let index = fetch_android_index(&client, &self.index_url)?;
        index.validate(&self.release_base_url)?;
        let apk = download_android_apk(&client, &self.state_root, &index, |current, total| {
            progress(AndroidProgress::DownloadingApk { current, total })
        })?;

        let installed = package_info(&device, ANDROID_PACKAGE)?;
        if force_reinstall && installed.is_some() {
            progress(AndroidProgress::RemovingOfficialInstall);
            uninstall_package(&device, ANDROID_PACKAGE)?;
        }

        progress(AndroidProgress::Installing);
        let replace = !force_reinstall && installed.is_some();
        match install_apk(&device, &apk, replace, &index.installer_package_name) {
            Ok(()) => {}
            Err(AndroidError::Adb(message))
                if replace && is_signature_incompatible_install(&message) =>
            {
                return Ok(AndroidInstallOutcome::NeedsReinstall {
                    device,
                    game_version: index.game_version,
                });
            }
            Err(error) => return Err(error),
        }

        progress(AndroidProgress::Verifying);
        verify_install(&device, &index.installer_package_name, &index.game_version)?;
        Ok(AndroidInstallOutcome::Installed {
            device,
            game_version: index.game_version,
        })
    }
}

fn http_client() -> Result<Client, AndroidError> {
    Ok(Client::builder()
        .user_agent(format!("AstralAutoPatcher/{}", env!("CARGO_PKG_VERSION")))
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(300))
        .build()?)
}

fn fetch_android_index(client: &Client, url: &str) -> Result<AndroidApkIndex, AndroidError> {
    let response = client
        .get(url)
        .header(CACHE_CONTROL, "no-cache")
        .send()?
        .error_for_status()?;
    if response
        .content_length()
        .is_some_and(|length| length > MAX_INDEX_BYTES)
    {
        return Err(AndroidError::InvalidIndex(
            "index response is too large".into(),
        ));
    }
    let mut raw = Vec::new();
    response.take(MAX_INDEX_BYTES + 1).read_to_end(&mut raw)?;
    if raw.len() as u64 > MAX_INDEX_BYTES {
        return Err(AndroidError::InvalidIndex(
            "index response is too large".into(),
        ));
    }
    Ok(serde_json::from_slice(&raw)?)
}

fn download_android_apk<F>(
    client: &Client,
    state_root: &Path,
    index: &AndroidApkIndex,
    mut progress: F,
) -> Result<PathBuf, AndroidError>
where
    F: FnMut(u64, u64),
{
    let root = state_root.join("android").join("apk");
    fs::create_dir_all(&root)?;
    let destination = root.join(format!("AstralParty_INT_Korean-{}.apk", index.game_version));
    if destination.is_file()
        && destination.metadata()?.len() == index.size
        && sha256_file(&destination)? == index.sha256
    {
        progress(index.size, index.size);
        return Ok(destination);
    }

    let temp = destination.with_extension("apk.download");
    let _ = fs::remove_file(&temp);
    let mut response = client.get(&index.download_url).send()?.error_for_status()?;
    if let Some(length) = response.content_length()
        && length != index.size
    {
        return Err(AndroidError::ApkSizeMismatch {
            expected: index.size,
            actual: length,
        });
    }
    let mut file = File::create(&temp)?;
    let mut hasher = Sha256::new();
    let mut actual = 0_u64;
    let mut buffer = [0_u8; 128 * 1024];
    progress(0, index.size);
    loop {
        let read = response.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        file.write_all(&buffer[..read])?;
        hasher.update(&buffer[..read]);
        actual = actual.saturating_add(read as u64);
        progress(actual.min(index.size), index.size);
        if actual > index.size {
            let _ = fs::remove_file(&temp);
            return Err(AndroidError::ApkSizeMismatch {
                expected: index.size,
                actual,
            });
        }
    }
    file.sync_all()?;
    drop(file);
    if actual != index.size {
        let _ = fs::remove_file(&temp);
        return Err(AndroidError::ApkSizeMismatch {
            expected: index.size,
            actual,
        });
    }
    if format!("{:x}", hasher.finalize()) != index.sha256 {
        let _ = fs::remove_file(&temp);
        return Err(AndroidError::ApkHashMismatch);
    }
    if destination.exists() {
        fs::remove_file(&destination)?;
    }
    fs::rename(&temp, &destination)?;
    Ok(destination)
}

fn sha256_file(path: &Path) -> Result<String, std::io::Error> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 128 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

#[cfg(windows)]
fn ensure_platform_tools(state_root: &Path) -> Result<PathBuf, AndroidError> {
    let tools_root = state_root.join("tools").join("platform-tools");
    let adb = tools_root.join("adb.exe");
    if adb.is_file() && adb_works(&adb) {
        return Ok(adb);
    }

    let download_root = state_root.join("tools");
    fs::create_dir_all(&download_root)?;
    let archive = download_root.join("platform-tools-windows.download.zip");
    let extract_root = download_root.join("platform-tools-extract");
    let _ = fs::remove_file(&archive);
    let _ = fs::remove_dir_all(&extract_root);

    let client = http_client()?;
    let mut response = client.get(PLATFORM_TOOLS_URL).send()?.error_for_status()?;
    if response
        .content_length()
        .is_some_and(|length| length > MAX_PLATFORM_TOOLS_BYTES)
    {
        return Err(AndroidError::PlatformTools(
            "download is unexpectedly large".into(),
        ));
    }
    let mut file = File::create(&archive)?;
    let mut total = 0_u64;
    let mut buffer = [0_u8; 128 * 1024];
    loop {
        let read = response.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        total = total.saturating_add(read as u64);
        if total > MAX_PLATFORM_TOOLS_BYTES {
            let _ = fs::remove_file(&archive);
            return Err(AndroidError::PlatformTools(
                "download is unexpectedly large".into(),
            ));
        }
        file.write_all(&buffer[..read])?;
    }
    file.sync_all()?;
    // Close the downloaded ZIP before another Windows process tries to open it.
    // Keeping the Rust file handle alive here can make Expand-Archive fail while
    // still leaving an empty extraction directory behind.
    drop(file);
    fs::create_dir_all(&extract_root)?;

    let script = format!(
        "$ErrorActionPreference = 'Stop'; try {{ Expand-Archive -LiteralPath '{}' -DestinationPath '{}' -Force -ErrorAction Stop }} catch {{ [Console]::Error.WriteLine($_.Exception.Message); exit 1 }}",
        escape_powershell_literal(&archive),
        escape_powershell_literal(&extract_root)
    );
    let output = run_command(
        Command::new("powershell.exe").args(["-NoProfile", "-NonInteractive", "-Command", &script]),
        Duration::from_secs(90),
    )?;
    if !output.status.success() {
        return Err(AndroidError::PlatformTools(output_text(&output)));
    }
    let candidates = find_named_files(&extract_root, "adb.exe", 4)?;
    let extracted = candidates
        .into_iter()
        .find(|candidate| adb_works(candidate))
        .and_then(|candidate| candidate.parent().map(Path::to_path_buf))
        .ok_or_else(|| {
            AndroidError::PlatformTools(
                "압축을 해제했지만 실행 가능한 adb.exe를 찾지 못했습니다".into(),
            )
        })?;
    let _ = fs::remove_dir_all(&tools_root);
    fs::rename(&extracted, &tools_root)?;
    let _ = fs::remove_dir_all(&extract_root);
    let _ = fs::remove_file(&archive);
    if !adb_works(&adb) {
        return Err(AndroidError::PlatformTools(
            "downloaded adb.exe could not be executed".into(),
        ));
    }
    Ok(adb)
}

#[cfg(not(windows))]
fn ensure_platform_tools(_state_root: &Path) -> Result<PathBuf, AndroidError> {
    Err(AndroidError::UnsupportedPlatform)
}

#[cfg(any(windows, test))]
fn find_named_files(
    root: &Path,
    file_name: &str,
    max_depth: usize,
) -> Result<Vec<PathBuf>, std::io::Error> {
    let mut matches = Vec::new();
    let mut pending = vec![(root.to_path_buf(), 0_usize)];
    while let Some((directory, depth)) = pending.pop() {
        for entry in fs::read_dir(directory)? {
            let entry = entry?;
            let file_type = entry.file_type()?;
            let path = entry.path();
            if file_type.is_file()
                && entry
                    .file_name()
                    .to_string_lossy()
                    .eq_ignore_ascii_case(file_name)
            {
                matches.push(path);
            } else if file_type.is_dir() && depth < max_depth {
                pending.push((path, depth + 1));
            }
        }
    }
    Ok(matches)
}

#[cfg(windows)]
fn escape_powershell_literal(path: &Path) -> String {
    path.to_string_lossy().replace('\'', "''")
}

#[cfg(windows)]
fn adb_works(adb: &Path) -> bool {
    run_adb(adb, &["version"], Duration::from_secs(5))
        .map(|output| output.status.success())
        .unwrap_or(false)
}

#[derive(Debug, Clone)]
struct AdbProvider {
    name: String,
    adb_path: PathBuf,
    emulator: bool,
    mumu_probe: bool,
}

fn discover_devices(primary_adb: Option<&Path>) -> Result<Vec<AndroidDevice>, AndroidError> {
    let mut providers = Vec::new();
    if let Some(primary_adb) = primary_adb {
        providers.push(AdbProvider {
            name: "Android USB/ADB".into(),
            adb_path: primary_adb.to_owned(),
            emulator: false,
            mumu_probe: false,
        });
    }
    providers.extend(app_player_providers());

    let mut unique = BTreeMap::<String, AndroidDevice>::new();
    for provider in providers {
        if !provider.adb_path.is_file() {
            continue;
        }
        let mut parsed = query_devices(&provider).unwrap_or_default();
        if parsed.is_empty() && provider.mumu_probe {
            try_mumu_connect(&provider.adb_path);
            parsed = query_devices(&provider).unwrap_or_default();
        }
        for device in parsed {
            unique.entry(device.serial.clone()).or_insert(device);
        }
    }
    Ok(unique.into_values().collect())
}

fn query_devices(provider: &AdbProvider) -> Result<Vec<AndroidDevice>, AndroidError> {
    let output = run_adb(&provider.adb_path, &["devices", "-l"], COMMAND_TIMEOUT)?;
    if !output.status.success() {
        return Err(AndroidError::Adb(output_text(&output)));
    }
    Ok(parse_adb_devices(
        &String::from_utf8_lossy(&output.stdout),
        &provider.name,
        &provider.adb_path,
        provider.emulator,
    ))
}

#[cfg(windows)]
fn app_player_providers() -> Vec<AdbProvider> {
    let mut values = Vec::new();
    let mut add = |name: &str, path: PathBuf, emulator: bool, mumu_probe: bool| {
        if path.is_file() {
            values.push(AdbProvider {
                name: name.into(),
                adb_path: path,
                emulator,
                mumu_probe,
            });
        }
    };

    for root in env_program_files_roots() {
        add(
            "MuMu Player",
            root.join("Netease/MuMuPlayer/nx_main/adb.exe"),
            true,
            true,
        );
        add(
            "MuMu Player",
            root.join("Netease/MuMuPlayer/nx_main/adb"),
            true,
            true,
        );
        add(
            "BlueStacks",
            root.join("BlueStacks_nxt/HD-Adb.exe"),
            true,
            false,
        );
        add(
            "BlueStacks",
            root.join("BlueStacks/HD-Adb.exe"),
            true,
            false,
        );
        add(
            "LDPlayer",
            root.join("LDPlayer/LDPlayer9/adb.exe"),
            true,
            false,
        );
        add("LDPlayer", root.join("dnplayerext2/adb.exe"), true, false);
    }
    values
}

#[cfg(not(windows))]
fn app_player_providers() -> Vec<AdbProvider> {
    Vec::new()
}

#[cfg(windows)]
fn env_program_files_roots() -> Vec<PathBuf> {
    let mut roots: Vec<PathBuf> = Vec::new();
    for key in ["ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"] {
        if let Some(value) = std::env::var_os(key) {
            let path = PathBuf::from(value);
            if !roots
                .iter()
                .any(|item| paths_equal(item.as_path(), path.as_path()))
            {
                roots.push(path);
            }
        }
    }
    roots
}

#[cfg(windows)]
fn paths_equal(left: &Path, right: &Path) -> bool {
    left.to_string_lossy()
        .eq_ignore_ascii_case(&right.to_string_lossy())
}

fn try_mumu_connect(adb: &Path) {
    for port in [7555_u16, 16384, 16416, 16512, 5555] {
        let address = SocketAddr::from(([127, 0, 0, 1], port));
        if TcpStream::connect_timeout(&address, Duration::from_millis(120)).is_err() {
            continue;
        }
        let endpoint = format!("127.0.0.1:{port}");
        let _ = run_adb(adb, &["connect", &endpoint], Duration::from_secs(3));
    }
}

fn parse_adb_devices(
    text: &str,
    provider: &str,
    adb_path: &Path,
    provider_is_emulator: bool,
) -> Vec<AndroidDevice> {
    let mut devices = Vec::new();
    for line in text.lines().map(str::trim) {
        if line.is_empty()
            || line.starts_with("List of devices")
            || line.starts_with('*')
            || line.starts_with("adb server")
        {
            continue;
        }
        let mut parts = line.split_whitespace();
        let Some(serial) = parts.next() else {
            continue;
        };
        let Some(state_raw) = parts.next() else {
            continue;
        };
        let mut model = String::new();
        for part in parts {
            if let Some(value) = part.strip_prefix("model:") {
                model = value.replace('_', " ");
            }
        }
        let looks_emulator = provider_is_emulator
            || serial.starts_with("emulator-")
            || serial.starts_with("127.0.0.1:")
            || serial.starts_with("localhost:");
        devices.push(AndroidDevice {
            serial: serial.to_string(),
            state: AndroidDeviceState::parse(state_raw),
            model,
            provider: provider.to_string(),
            kind: if looks_emulator {
                AndroidDeviceKind::Emulator
            } else {
                AndroidDeviceKind::Physical
            },
            adb_path: adb_path.to_owned(),
        });
    }
    devices
}

fn select_target_device(devices: &[AndroidDevice]) -> Result<AndroidDevice, AndroidError> {
    let ready = devices
        .iter()
        .filter(|device| device.state == AndroidDeviceState::Device)
        .cloned()
        .collect::<Vec<_>>();
    match ready.as_slice() {
        [device] => return Ok(device.clone()),
        [] => {
            if let Some(device) = devices
                .iter()
                .find(|device| device.state == AndroidDeviceState::Unauthorized)
            {
                return Err(AndroidError::UnauthorizedDevice(device.display_name()));
            }
            if let Some(device) = devices
                .iter()
                .find(|device| device.state == AndroidDeviceState::Offline)
            {
                return Err(AndroidError::OfflineDevice(device.display_name()));
            }
            return Err(AndroidError::NoDevice);
        }
        _ => {}
    }

    let with_game = ready
        .iter()
        .filter(|device| {
            package_info(device, ANDROID_PACKAGE)
                .ok()
                .flatten()
                .is_some()
        })
        .cloned()
        .collect::<Vec<_>>();
    if let [device] = with_game.as_slice() {
        return Ok(device.clone());
    }

    Err(AndroidError::MultipleDevices(
        ready
            .iter()
            .map(AndroidDevice::display_name)
            .collect::<Vec<_>>()
            .join(", "),
    ))
}

fn package_info(
    device: &AndroidDevice,
    package: &str,
) -> Result<Option<AndroidPackageInfo>, AndroidError> {
    let command =
        format!("dumpsys package {package} | grep -E 'versionName=|installerPackageName='");
    let output = run_adb(
        &device.adb_path,
        &["-s", &device.serial, "shell", "sh", "-c", &command],
        COMMAND_TIMEOUT,
    )?;
    if !output.status.success() {
        return Ok(None);
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let info = parse_package_info(&text);
    if info.version_name.is_none() && info.installer_package_name.is_none() {
        return Ok(None);
    }
    Ok(Some(info))
}

fn parse_package_info(text: &str) -> AndroidPackageInfo {
    let mut version_name = None;
    let mut installer_package_name = None;
    for line in text.lines().map(str::trim) {
        if version_name.is_none()
            && let Some(value) = line.strip_prefix("versionName=")
        {
            version_name = Some(value.trim().to_string());
        }
        if installer_package_name.is_none()
            && let Some(value) = line.strip_prefix("installerPackageName=")
        {
            let value = value.trim();
            if !value.is_empty() && value != "null" {
                installer_package_name = Some(value.to_string());
            }
        }
    }
    AndroidPackageInfo {
        version_name,
        installer_package_name,
    }
}

fn install_apk(
    device: &AndroidDevice,
    apk: &Path,
    replace: bool,
    installer: &str,
) -> Result<(), AndroidError> {
    let mut args = vec![
        OsString::from("-s"),
        OsString::from(&device.serial),
        OsString::from("install"),
    ];
    if replace {
        args.push(OsString::from("-r"));
    }
    args.extend([
        OsString::from("-i"),
        OsString::from(installer),
        apk.as_os_str().to_owned(),
    ]);
    let output = run_adb_os(&device.adb_path, &args, INSTALL_TIMEOUT)?;
    ensure_success(output, "APK 설치")
}

fn uninstall_package(device: &AndroidDevice, package: &str) -> Result<(), AndroidError> {
    let output = run_adb(
        &device.adb_path,
        &["-s", &device.serial, "uninstall", package],
        INSTALL_TIMEOUT,
    )?;
    ensure_success(output, "기존 앱 제거")
}

fn verify_install(
    device: &AndroidDevice,
    expected_installer: &str,
    expected_version: &str,
) -> Result<(), AndroidError> {
    let info = package_info(device, ANDROID_PACKAGE)?
        .ok_or_else(|| AndroidError::Adb("설치된 게임 패키지를 찾지 못했습니다".into()))?;
    let actual_installer = info
        .installer_package_name
        .unwrap_or_else(|| "<none>".into());
    if actual_installer != expected_installer {
        return Err(AndroidError::InstallerMismatch {
            expected: expected_installer.into(),
            actual: actual_installer,
        });
    }
    let actual_version = info.version_name.unwrap_or_else(|| "<none>".into());
    if actual_version != expected_version {
        return Err(AndroidError::InstalledVersionMismatch {
            expected: expected_version.into(),
            actual: actual_version,
        });
    }
    Ok(())
}

fn is_signature_incompatible_install(message: &str) -> bool {
    message.contains("INSTALL_FAILED_UPDATE_INCOMPATIBLE")
        || message.contains("signatures do not match")
        || message.contains("signature mismatch")
}

fn run_adb(adb: &Path, args: &[&str], timeout: Duration) -> Result<Output, AndroidError> {
    let args = args.iter().map(OsString::from).collect::<Vec<_>>();
    run_adb_os(adb, &args, timeout)
}

fn run_adb_os(adb: &Path, args: &[OsString], timeout: Duration) -> Result<Output, AndroidError> {
    run_command(Command::new(adb).args(args), timeout).map_err(AndroidError::Io)
}

fn run_command(command: &mut Command, timeout: Duration) -> Result<Output, std::io::Error> {
    let mut child = command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let deadline = Instant::now() + timeout;
    loop {
        if child.try_wait()?.is_some() {
            return child.wait_with_output();
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let output = child.wait_with_output()?;
            return Err(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                format!("command timed out: {}", output_text(&output)),
            ));
        }
        thread::sleep(Duration::from_millis(50));
    }
}

fn ensure_success(output: Output, action: &str) -> Result<(), AndroidError> {
    if output.status.success() && String::from_utf8_lossy(&output.stdout).contains("Success") {
        return Ok(());
    }
    Err(AndroidError::Adb(format!(
        "{action}: {}",
        output_text(&output)
    )))
}

fn output_text(output: &Output) -> String {
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    match (stdout.is_empty(), stderr.is_empty()) {
        (false, false) => format!("{stdout} · {stderr}"),
        (false, true) => stdout,
        (true, false) => stderr,
        (true, true) => format!("exit status {}", output.status),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_adb_devices_and_classifies_emulators() {
        let devices = parse_adb_devices(
            "List of devices attached\nR3CT10\tdevice product:e3q model:SM_S938N device:e3q transport_id:1\n127.0.0.1:7555\tdevice model:MuMu_Emulator transport_id:2\nABC\tunauthorized usb:1-2\n",
            "Android USB/ADB",
            Path::new("adb.exe"),
            false,
        );
        assert_eq!(devices.len(), 3);
        assert_eq!(devices[0].model, "SM S938N");
        assert_eq!(devices[0].kind, AndroidDeviceKind::Physical);
        assert_eq!(devices[1].kind, AndroidDeviceKind::Emulator);
        assert_eq!(devices[2].state, AndroidDeviceState::Unauthorized);
    }

    #[test]
    fn finds_adb_inside_nested_platform_tools_layout() {
        let temp = tempfile::tempdir().expect("tempdir");
        let nested = temp
            .path()
            .join("platform-tools_r37.0.1-windows")
            .join("platform-tools");
        fs::create_dir_all(&nested).expect("create nested tools");
        fs::write(nested.join("adb.exe"), b"adb").expect("write adb");

        let matches = find_named_files(temp.path(), "adb.exe", 4).expect("search");
        assert_eq!(matches, vec![nested.join("adb.exe")]);
    }

    #[test]
    fn parses_package_version_and_installer() {
        let info = parse_package_info(
            "Packages:\n  Package [com.feimo.astralpartyjpn]\n    versionName=3.2.0\n    installerPackageName=com.android.vending\n",
        );
        assert_eq!(info.version_name.as_deref(), Some("3.2.0"));
        assert_eq!(
            info.installer_package_name.as_deref(),
            Some("com.android.vending")
        );
    }

    #[test]
    fn index_rejects_wrong_installer_and_untrusted_url() {
        let mut index = AndroidApkIndex {
            schema_version: 1,
            package_name: ANDROID_PACKAGE.into(),
            game_version: "3.2.0".into(),
            download_url: "https://github.com/example/repo/releases/download/android-v1/AstralParty_INT_Korean.apk".into(),
            sha256: "a".repeat(64),
            size: 123,
            installer_package_name: PLAY_INSTALLER_PACKAGE.into(),
        };
        assert!(
            index
                .validate("https://github.com/example/repo/releases/download")
                .is_ok()
        );
        index.installer_package_name = "example.installer".into();
        assert!(
            index
                .validate("https://github.com/example/repo/releases/download")
                .is_err()
        );
    }

    #[test]
    fn signature_mismatch_detection_covers_package_manager_messages() {
        assert!(is_signature_incompatible_install(
            "Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: Package signatures do not match]"
        ));
        assert!(!is_signature_incompatible_install(
            "Failure [INSTALL_FAILED_VERSION_DOWNGRADE]"
        ));
    }
}
