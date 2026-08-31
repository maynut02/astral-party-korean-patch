#[cfg(windows)]
use std::ffi::OsStr;
use std::ffi::OsString;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
#[cfg(windows)]
use std::thread;
use std::time::Duration;

use reqwest::blocking::Client;
use reqwest::header::CACHE_CONTROL;
use semver::Version;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use thiserror::Error;

#[cfg(windows)]
use crate::registration::installed_exe_path;

const INDEX_SCHEMA_VERSION: u32 = 1;
const MAX_INDEX_BYTES: u64 = 64 * 1024;
const UPDATE_DIR_NAME: &str = "updates";
const RELEASE_EXE_NAME: &str = "AstralWindowsPatcher.exe";
const APPLY_UPDATE_ARG: &str = "--apply-update";
const ARG_SEPARATOR: &str = "--";

#[derive(Debug, Error)]
pub enum UpdateError {
    #[error("HTTP error: {0}")]
    Http(#[from] reqwest::Error),
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("invalid semantic version: {0}")]
    Semver(#[from] semver::Error),
    #[error("patcher index response is too large")]
    IndexTooLarge,
    #[error("unsupported patcher index schema: {0}")]
    UnsupportedSchema(u32),
    #[error("invalid patcher index SHA-256")]
    InvalidSha256,
    #[error("invalid patcher index size")]
    InvalidSize,
    #[error("unexpected patcher download URL: {0}")]
    UnexpectedDownloadUrl(String),
    #[error("patcher download size mismatch: expected {expected}, actual {actual}")]
    DownloadSizeMismatch { expected: u64, actual: u64 },
    #[error("patcher download SHA-256 mismatch: expected {expected}, actual {actual}")]
    DownloadHashMismatch { expected: String, actual: String },
    #[error("invalid internal updater arguments")]
    InvalidUpdaterArguments,
    #[error("timed out while replacing the installed patcher")]
    ReplaceTimeout,
    #[error("automatic updater is only supported on Windows")]
    UnsupportedPlatform,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PatcherIndex {
    pub schema_version: u32,
    pub version: String,
    pub download_url: String,
    pub sha256: String,
    pub size: u64,
}

impl PatcherIndex {
    pub fn validate(&self, release_base_url: &str) -> Result<Version, UpdateError> {
        if self.schema_version != INDEX_SCHEMA_VERSION {
            return Err(UpdateError::UnsupportedSchema(self.schema_version));
        }
        let version = Version::parse(&self.version)?;
        if self.sha256.len() != 64
            || !self
                .sha256
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(UpdateError::InvalidSha256);
        }
        if self.size == 0 {
            return Err(UpdateError::InvalidSize);
        }
        let expected_url = format!(
            "{}/windows-patcher-v{}/{}",
            release_base_url.trim_end_matches('/'),
            version,
            RELEASE_EXE_NAME
        );
        if self.download_url != expected_url {
            return Err(UpdateError::UnexpectedDownloadUrl(
                self.download_url.clone(),
            ));
        }
        Ok(version)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApplyUpdateRequest {
    pub previous_pid: u32,
    pub original_args: Vec<OsString>,
}

pub fn parse_apply_update_request(
    args: &[OsString],
) -> Result<Option<ApplyUpdateRequest>, UpdateError> {
    if args.first().and_then(|arg| arg.to_str()) != Some(APPLY_UPDATE_ARG) {
        return Ok(None);
    }
    let previous_pid = args
        .get(1)
        .and_then(|arg| arg.to_str())
        .and_then(|value| value.parse::<u32>().ok())
        .ok_or(UpdateError::InvalidUpdaterArguments)?;
    if args.get(2).and_then(|arg| arg.to_str()) != Some(ARG_SEPARATOR) {
        return Err(UpdateError::InvalidUpdaterArguments);
    }
    Ok(Some(ApplyUpdateRequest {
        previous_pid,
        original_args: args[3..].to_vec(),
    }))
}

pub fn check_and_launch_update(
    index_url: &str,
    release_base_url: &str,
    state_root: &Path,
    original_args: &[OsString],
) -> Result<bool, UpdateError> {
    cleanup_old_update_files(state_root);

    let user_agent = format!("AstralAutoPatcher/{}", env!("CARGO_PKG_VERSION"));
    let client = Client::builder()
        .user_agent(user_agent)
        .connect_timeout(Duration::from_secs(5))
        .timeout(Duration::from_secs(90))
        .build()?;
    let index = fetch_index(&client, index_url)?;
    let latest = index.validate(release_base_url)?;
    let current = Version::parse(env!("CARGO_PKG_VERSION"))?;
    if latest <= current {
        return Ok(false);
    }

    let helper = download_update(&client, state_root, &index)?;
    let mut command = Command::new(&helper);
    command
        .arg(APPLY_UPDATE_ARG)
        .arg(std::process::id().to_string())
        .arg(ARG_SEPARATOR)
        .args(original_args);
    command.spawn()?;
    Ok(true)
}

fn fetch_index(client: &Client, index_url: &str) -> Result<PatcherIndex, UpdateError> {
    let response = client
        .get(index_url)
        .header(CACHE_CONTROL, "no-cache")
        .send()?
        .error_for_status()?;
    if response
        .content_length()
        .is_some_and(|length| length > MAX_INDEX_BYTES)
    {
        return Err(UpdateError::IndexTooLarge);
    }
    let mut raw = Vec::new();
    response.take(MAX_INDEX_BYTES + 1).read_to_end(&mut raw)?;
    if raw.len() as u64 > MAX_INDEX_BYTES {
        return Err(UpdateError::IndexTooLarge);
    }
    Ok(serde_json::from_slice(&raw)?)
}

fn download_update(
    client: &Client,
    state_root: &Path,
    index: &PatcherIndex,
) -> Result<PathBuf, UpdateError> {
    let update_root = state_root.join(UPDATE_DIR_NAME);
    fs::create_dir_all(&update_root)?;
    let helper = update_root.join(format!("AstralAutoPatcher-{}.exe", index.version));
    let temp = update_root.join(format!("AstralAutoPatcher-{}.download", index.version));
    let _ = fs::remove_file(&temp);
    let _ = fs::remove_file(&helper);

    let mut response = client.get(&index.download_url).send()?.error_for_status()?;
    if response
        .content_length()
        .is_some_and(|length| length != index.size)
    {
        return Err(UpdateError::DownloadSizeMismatch {
            expected: index.size,
            actual: response.content_length().unwrap_or_default(),
        });
    }

    let mut file = File::create(&temp)?;
    let mut hasher = Sha256::new();
    let mut actual_size = 0_u64;
    let mut buffer = [0_u8; 128 * 1024];
    loop {
        let read = response.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        file.write_all(&buffer[..read])?;
        hasher.update(&buffer[..read]);
        actual_size = actual_size.saturating_add(read as u64);
        if actual_size > index.size {
            let _ = fs::remove_file(&temp);
            return Err(UpdateError::DownloadSizeMismatch {
                expected: index.size,
                actual: actual_size,
            });
        }
    }
    file.sync_all()?;
    drop(file);

    if actual_size != index.size {
        let _ = fs::remove_file(&temp);
        return Err(UpdateError::DownloadSizeMismatch {
            expected: index.size,
            actual: actual_size,
        });
    }
    let actual_hash = format!("{:x}", hasher.finalize());
    if actual_hash != index.sha256 {
        let _ = fs::remove_file(&temp);
        return Err(UpdateError::DownloadHashMismatch {
            expected: index.sha256.clone(),
            actual: actual_hash,
        });
    }

    fs::rename(&temp, &helper)?;
    Ok(helper)
}

fn cleanup_old_update_files(state_root: &Path) {
    let update_root = state_root.join(UPDATE_DIR_NAME);
    let Ok(entries) = fs::read_dir(update_root) else {
        return;
    };
    for entry in entries.flatten() {
        let _ = fs::remove_file(entry.path());
    }
}

#[cfg(windows)]
pub fn apply_update_and_restart(
    state_root: &Path,
    request: ApplyUpdateRequest,
) -> Result<(), UpdateError> {
    use windows_sys::Win32::Storage::FileSystem::{
        MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH, MoveFileExW,
    };

    let current = std::env::current_exe()?;
    fs::create_dir_all(state_root)?;
    let installed = installed_exe_path(state_root);
    let replacement = state_root.join("AstralAutoPatcher.replace.tmp.exe");
    let _ = fs::remove_file(&replacement);
    fs::copy(&current, &replacement)?;
    File::options().write(true).open(&replacement)?.sync_all()?;

    wait_for_previous_process(request.previous_pid);

    let from = wide_null(replacement.as_os_str());
    let to = wide_null(installed.as_os_str());
    let mut replaced = false;
    for _ in 0..200 {
        let success = unsafe {
            MoveFileExW(
                from.as_ptr(),
                to.as_ptr(),
                MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
            )
        };
        if success != 0 {
            replaced = true;
            break;
        }
        thread::sleep(Duration::from_millis(50));
    }
    if !replaced {
        let _ = fs::remove_file(&replacement);
        return Err(UpdateError::ReplaceTimeout);
    }

    Command::new(&installed)
        .args(&request.original_args)
        .spawn()?;
    Ok(())
}

#[cfg(windows)]
fn wait_for_previous_process(pid: u32) {
    use windows_sys::Win32::Foundation::{CloseHandle, WAIT_OBJECT_0};
    use windows_sys::Win32::System::Threading::{
        OpenProcess, PROCESS_SYNCHRONIZE, WaitForSingleObject,
    };

    let handle = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, pid) };
    if handle.is_null() {
        return;
    }
    let result = unsafe { WaitForSingleObject(handle, 30_000) };
    unsafe { CloseHandle(handle) };
    if result != WAIT_OBJECT_0 {
        thread::sleep(Duration::from_millis(250));
    }
}

#[cfg(windows)]
fn wide_null(value: &OsStr) -> Vec<u16> {
    use std::os::windows::ffi::OsStrExt;
    value.encode_wide().chain(std::iter::once(0)).collect()
}

#[cfg(not(windows))]
pub fn apply_update_and_restart(
    _state_root: &Path,
    _request: ApplyUpdateRequest,
) -> Result<(), UpdateError> {
    Err(UpdateError::UnsupportedPlatform)
}

#[cfg(test)]
mod tests {
    use super::*;

    const RELEASE_BASE: &str = "https://github.com/example/repo/releases/download";

    fn index(version: &str) -> PatcherIndex {
        PatcherIndex {
            schema_version: 1,
            version: version.into(),
            download_url: format!(
                "{RELEASE_BASE}/windows-patcher-v{version}/AstralWindowsPatcher.exe"
            ),
            sha256: "a".repeat(64),
            size: 123,
        }
    }

    #[test]
    fn validates_exact_release_asset_url() {
        assert_eq!(
            index("0.6.0").validate(RELEASE_BASE).unwrap(),
            Version::parse("0.6.0").unwrap()
        );
        let mut bad = index("0.6.0");
        bad.download_url = "https://example.test/AstralWindowsPatcher.exe".into();
        assert!(matches!(
            bad.validate(RELEASE_BASE),
            Err(UpdateError::UnexpectedDownloadUrl(_))
        ));
    }

    #[test]
    fn rejects_invalid_checksum_and_zero_size() {
        let mut bad_hash = index("0.6.0");
        bad_hash.sha256 = "ABC".into();
        assert!(matches!(
            bad_hash.validate(RELEASE_BASE),
            Err(UpdateError::InvalidSha256)
        ));

        let mut bad_size = index("0.6.0");
        bad_size.size = 0;
        assert!(matches!(
            bad_size.validate(RELEASE_BASE),
            Err(UpdateError::InvalidSize)
        ));
    }

    #[test]
    fn parses_internal_apply_request_and_preserves_original_uri() {
        let args = vec![
            OsString::from(APPLY_UPDATE_ARG),
            OsString::from("1234"),
            OsString::from(ARG_SEPARATOR),
            OsString::from("astral://install"),
        ];
        let request = parse_apply_update_request(&args).unwrap().unwrap();
        assert_eq!(request.previous_pid, 1234);
        assert_eq!(
            request.original_args,
            vec![OsString::from("astral://install")]
        );
    }

    #[test]
    fn normal_args_are_not_internal_updater_requests() {
        let args = vec![OsString::from("astral://settings")];
        assert!(parse_apply_update_request(&args).unwrap().is_none());
    }
}
