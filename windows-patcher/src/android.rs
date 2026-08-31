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

use crate::logging;

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
const MAX_ERROR_STREAM_CHARS: usize = 2048;

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
        let expected_url = format!(
            "{}/int-apk-v{}/AstralPartyPatch.apk",
            release_base_url.trim_end_matches('/'),
            self.game_version
        );
        if self.download_url != expected_url {
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

    pub fn selection_name(&self) -> String {
        format!("{} · {}", self.display_name(), self.serial)
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
    NeedsDeviceSelection {
        devices: Vec<AndroidDevice>,
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
        selected_serial: Option<&str>,
        mut progress: F,
    ) -> Result<AndroidInstallOutcome, AndroidError>
    where
        F: FnMut(AndroidProgress),
    {
        logging::info("Android install started");
        progress(AndroidProgress::PreparingAdb);
        let (primary_adb, platform_tools_error) = match ensure_platform_tools(&self.state_root) {
            Ok(adb) => (Some(adb), None),
            Err(error) => (None, Some(error)),
        };
        progress(AndroidProgress::DiscoveringDevices);
        let devices = dedupe_same_devices(discover_devices(primary_adb.as_deref())?);
        logging::info(format!(
            "Android device discovery: {} candidate(s)",
            devices.len()
        ));
        if devices.is_empty()
            && let Some(error) = platform_tools_error
        {
            return Err(error);
        }
        let device = match select_target_device(&devices, selected_serial)? {
            DeviceSelection::Selected(device) => device,
            DeviceSelection::Choose(devices) => {
                return Ok(AndroidInstallOutcome::NeedsDeviceSelection { devices });
            }
        };
        logging::info(format!(
            "Android device selected: name={} provider={}",
            device.display_name(),
            device.provider
        ));
        progress(AndroidProgress::DeviceSelected {
            name: device.display_name(),
        });

        progress(AndroidProgress::ResolvingApk);
        let client = http_client()?;
        let index = fetch_android_index(&client, &self.index_url)?;
        index.validate(&self.release_base_url)?;
        logging::info(format!(
            "Android APK resolved: game_version={} size={}",
            index.game_version, index.size
        ));
        let apk = download_android_apk(&client, &self.state_root, &index, |current, total| {
            progress(AndroidProgress::DownloadingApk { current, total })
        })?;

        let installed = package_info(&device, ANDROID_PACKAGE)?;
        logging::info(format!(
            "Android existing package: installed={} version={}",
            installed.is_some(),
            installed
                .as_ref()
                .and_then(|info| info.version_name.as_deref())
                .unwrap_or("<none>")
        ));
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
        let actual_version =
            verify_install(&device, &index.installer_package_name, &index.game_version)?;
        logging::info(format!(
            "Android install verified: device={} version={actual_version}",
            device.display_name()
        ));
        Ok(AndroidInstallOutcome::Installed {
            device,
            game_version: actual_version,
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
    let root = state_root.join("routes").join("int-android").join("apk");
    fs::create_dir_all(&root)?;
    let destination = root.join(format!(
        "AstralParty_INT_ANDROID-{}.apk",
        index.game_version
    ));
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
    let android_root = state_root.join("routes").join("int-android");
    let tools_root = android_root.join("tools").join("platform-tools");
    let adb = tools_root.join("adb.exe");
    if adb.is_file() && adb_works(&adb) {
        return Ok(adb);
    }

    let download_root = android_root.join("tools");
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

enum DeviceSelection {
    Selected(AndroidDevice),
    Choose(Vec<AndroidDevice>),
}

fn dedupe_same_devices(devices: Vec<AndroidDevice>) -> Vec<AndroidDevice> {
    let mut unique = BTreeMap::<String, AndroidDevice>::new();
    for device in devices {
        let key = if device.state == AndroidDeviceState::Device
            && device.kind == AndroidDeviceKind::Physical
        {
            physical_device_id(&device)
                .map(|value| format!("physical:{value}"))
                .unwrap_or_else(|| format!("serial:{}", device.serial))
        } else {
            format!("serial:{}", device.serial)
        };
        match unique.get(&key) {
            Some(existing) if prefer_existing_connection(existing, &device) => {}
            _ => {
                unique.insert(key, device);
            }
        }
    }
    unique.into_values().collect()
}

fn physical_device_id(device: &AndroidDevice) -> Option<String> {
    let output = run_adb(
        &device.adb_path,
        &["-s", &device.serial, "shell", "getprop", "ro.serialno"],
        Duration::from_secs(5),
    )
    .ok()?;
    if !output.status.success() {
        return None;
    }
    let value = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if value.is_empty() || value.eq_ignore_ascii_case("unknown") {
        None
    } else {
        Some(value)
    }
}

fn prefer_existing_connection(existing: &AndroidDevice, candidate: &AndroidDevice) -> bool {
    !is_network_serial(&existing.serial) || is_network_serial(&candidate.serial)
}

fn is_network_serial(serial: &str) -> bool {
    serial.contains(':')
        || serial.contains("_adb-tls-connect")
        || serial.ends_with("._tcp")
        || serial.ends_with(".local")
}

fn select_target_device(
    devices: &[AndroidDevice],
    selected_serial: Option<&str>,
) -> Result<DeviceSelection, AndroidError> {
    let ready = devices
        .iter()
        .filter(|device| device.state == AndroidDeviceState::Device)
        .cloned()
        .collect::<Vec<_>>();

    if let Some(selected_serial) = selected_serial {
        return ready
            .into_iter()
            .find(|device| device.serial == selected_serial)
            .map(DeviceSelection::Selected)
            .ok_or_else(|| {
                AndroidError::Adb(format!(
                    "선택한 Android 기기 {selected_serial}가 더 이상 연결되어 있지 않습니다"
                ))
            });
    }

    match ready.as_slice() {
        [device] => return Ok(DeviceSelection::Selected(device.clone())),
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
    match with_game.as_slice() {
        [device] => Ok(DeviceSelection::Selected(device.clone())),
        [] => Ok(DeviceSelection::Choose(ready)),
        _ => Ok(DeviceSelection::Choose(with_game)),
    }
}

fn package_info(
    device: &AndroidDevice,
    package: &str,
) -> Result<Option<AndroidPackageInfo>, AndroidError> {
    // Avoid `adb shell sh -c ...` here. adb does not preserve argv quoting like a local
    // process launcher, so the remote shell can interpret only `dumpsys` as the `-c` command
    // and dump every Android service. Query PackageManager directly and parse the requested
    // package block locally instead.
    let output = run_adb(
        &device.adb_path,
        &["-s", &device.serial, "shell", "dumpsys", "package", package],
        COMMAND_TIMEOUT,
    )?;
    if !output.status.success() {
        return Ok(None);
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let info = parse_package_info(&text, package);
    if info.version_name.is_none() && info.installer_package_name.is_none() {
        return Ok(None);
    }
    Ok(Some(info))
}

fn parse_package_info(text: &str, package: &str) -> AndroidPackageInfo {
    let package_marker = format!("Package [{package}]");
    let mut target_package_seen = false;
    let mut in_target_package = false;
    let mut target_version_name = None;
    let mut target_installer_package_name = None;
    let mut fallback_version_names = Vec::new();
    let mut fallback_installer_package_name = None;

    for line in text.lines().map(str::trim) {
        if line.starts_with("Package [") {
            in_target_package = line.starts_with(&package_marker);
            target_package_seen |= in_target_package;
        }

        if let Some(value) = line.strip_prefix("versionName=") {
            let value = value.trim();
            if !value.is_empty() && value != "null" {
                if in_target_package && target_version_name.is_none() {
                    target_version_name = Some(value.to_string());
                }
                fallback_version_names.push(value.to_string());
            }
        }

        if let Some(value) = line.strip_prefix("installerPackageName=") {
            let value = value.trim();
            if !value.is_empty() && value != "null" {
                if in_target_package && target_installer_package_name.is_none() {
                    target_installer_package_name = Some(value.to_string());
                }
                if fallback_installer_package_name.is_none() {
                    fallback_installer_package_name = Some(value.to_string());
                }
            }
        }
    }

    if target_package_seen {
        return AndroidPackageInfo {
            version_name: target_version_name,
            installer_package_name: target_installer_package_name,
        };
    }

    let version_name = fallback_version_names
        .iter()
        .find(|value| looks_like_game_version(value))
        .cloned()
        .or_else(|| fallback_version_names.into_iter().next());
    AndroidPackageInfo {
        version_name,
        installer_package_name: fallback_installer_package_name,
    }
}

fn looks_like_game_version(value: &str) -> bool {
    let parts = value.split('.').collect::<Vec<_>>();
    parts.len() >= 2
        && parts
            .iter()
            .all(|part| !part.is_empty() && part.bytes().all(|byte| byte.is_ascii_digit()))
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
) -> Result<String, AndroidError> {
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
    if !expected_version.eq_ignore_ascii_case("unknown") && actual_version != expected_version {
        return Err(AndroidError::InstalledVersionMismatch {
            expected: expected_version.into(),
            actual: actual_version,
        });
    }
    Ok(actual_version)
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
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| std::io::Error::other("failed to capture command stdout"))?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| std::io::Error::other("failed to capture command stderr"))?;
    let stdout_reader = spawn_output_reader(stdout);
    let stderr_reader = spawn_output_reader(stderr);

    let deadline = Instant::now() + timeout;
    let (status, timed_out) = loop {
        if let Some(status) = child.try_wait()? {
            break (status, false);
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            break (child.wait()?, true);
        }
        thread::sleep(Duration::from_millis(50));
    };

    let stdout = join_output_reader(stdout_reader)?;
    let stderr = join_output_reader(stderr_reader)?;
    let output = Output {
        status,
        stdout,
        stderr,
    };
    if timed_out {
        return Err(std::io::Error::new(
            std::io::ErrorKind::TimedOut,
            format!(
                "command timed out after {:.1}s: {}",
                timeout.as_secs_f64(),
                output_text(&output)
            ),
        ));
    }
    Ok(output)
}

fn spawn_output_reader<R>(mut reader: R) -> thread::JoinHandle<Result<Vec<u8>, std::io::Error>>
where
    R: Read + Send + 'static,
{
    thread::spawn(move || {
        let mut output = Vec::new();
        reader.read_to_end(&mut output)?;
        Ok(output)
    })
}

fn join_output_reader(
    handle: thread::JoinHandle<Result<Vec<u8>, std::io::Error>>,
) -> Result<Vec<u8>, std::io::Error> {
    handle
        .join()
        .map_err(|_| std::io::Error::other("command output reader panicked"))?
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
    let stdout = clipped_output_text(&output.stdout);
    let stderr = clipped_output_text(&output.stderr);
    match (stdout.is_empty(), stderr.is_empty()) {
        (false, false) => format!("{stdout} · {stderr}"),
        (false, true) => stdout,
        (true, false) => stderr,
        (true, true) => format!("exit status {}", output.status),
    }
}

fn clipped_output_text(raw: &[u8]) -> String {
    let text = String::from_utf8_lossy(raw);
    let text = text.trim();
    let count = text.chars().count();
    if count <= MAX_ERROR_STREAM_CHARS {
        return text.to_owned();
    }

    let edge = MAX_ERROR_STREAM_CHARS / 2;
    let head = text.chars().take(edge).collect::<String>();
    let tail = text
        .chars()
        .skip(count.saturating_sub(edge))
        .collect::<String>();
    format!(
        "{head}\n… 중간 출력 {}자 생략 …\n{tail}",
        count.saturating_sub(edge * 2)
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn command_output_fixture() {
        if std::env::var_os("ASTRAL_COMMAND_OUTPUT_FIXTURE").is_none() {
            return;
        }
        let payload = vec![b'x'; 1024 * 1024];
        std::io::stdout().write_all(&payload).unwrap();
    }

    #[test]
    fn command_runner_drains_large_output_while_process_is_running() {
        let executable = std::env::current_exe().unwrap();
        let mut command = Command::new(executable);
        command
            .args([
                "--exact",
                "android::tests::command_output_fixture",
                "--nocapture",
            ])
            .env("ASTRAL_COMMAND_OUTPUT_FIXTURE", "1");

        let output = run_command(&mut command, Duration::from_secs(10)).unwrap();
        assert!(output.status.success());
        assert!(output.stdout.len() >= 1024 * 1024);
    }

    #[test]
    fn long_command_output_is_clipped_for_user_facing_errors() {
        let raw = format!("HEAD{}TAIL", "x".repeat(MAX_ERROR_STREAM_CHARS * 2));
        let clipped = clipped_output_text(raw.as_bytes());
        assert!(clipped.starts_with("HEAD"));
        assert!(clipped.ends_with("TAIL"));
        assert!(clipped.contains("중간 출력"));
        assert!(clipped.chars().count() < raw.chars().count());
    }

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
    fn prefers_direct_usb_over_network_serial_for_same_device() {
        let direct = AndroidDevice {
            serial: "R3CT10".into(),
            state: AndroidDeviceState::Device,
            model: "SM F741N".into(),
            provider: "Android USB/ADB".into(),
            kind: AndroidDeviceKind::Physical,
            adb_path: PathBuf::from("adb.exe"),
        };
        let wireless = AndroidDevice {
            serial: "192.168.0.10:37123".into(),
            ..direct.clone()
        };
        assert!(prefer_existing_connection(&direct, &wireless));
        assert!(!prefer_existing_connection(&wireless, &direct));
        assert!(is_network_serial(&wireless.serial));
        assert!(!is_network_serial(&direct.serial));
    }

    #[test]
    fn parses_package_version_and_installer() {
        let info = parse_package_info(
            "Packages:\n  versionName=1.0\n  Package [com.feimo.astralpartyjpn] (abc):\n    versionName=3.2.0\n    installerPackageName=com.android.vending\n",
            ANDROID_PACKAGE,
        );
        assert_eq!(info.version_name.as_deref(), Some("3.2.0"));
        assert_eq!(
            info.installer_package_name.as_deref(),
            Some("com.android.vending")
        );
    }

    #[test]
    fn package_parser_ignores_other_version_names_before_target_package() {
        let info = parse_package_info(
            "Verifier:\n  versionName=1.0\nPackages:\n  Package [other.package] (111):\n    versionName=9.9.9\n  Package [com.feimo.astralpartyjpn] (222):\n    versionCode=302000\n    versionName=3.2.0\n    installerPackageName=com.android.vending\n",
            ANDROID_PACKAGE,
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
            download_url: "https://github.com/example/repo/releases/download/int-apk-v3.2.0/AstralPartyPatch.apk".into(),
            sha256: "a".repeat(64),
            size: 123,
            installer_package_name: PLAY_INSTALLER_PACKAGE.into(),
        };
        assert!(
            index
                .validate("https://github.com/example/repo/releases/download")
                .is_ok()
        );
        index.download_url =
            "https://github.com/example/repo/releases/download/int-apk-v3.2.0/other.apk".into();
        assert!(
            index
                .validate("https://github.com/example/repo/releases/download")
                .is_err()
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
