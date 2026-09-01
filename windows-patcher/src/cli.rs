use std::io;

use thiserror::Error;

#[cfg(windows)]
use crate::logging;
use crate::registration::RegistrationError;
#[cfg(windows)]
use crate::registration::ensure_self_installed_and_registered;
use crate::service::ServiceError;
#[cfg(windows)]
use crate::service::{PatcherPaths, migrate_legacy_state};
#[cfg(windows)]
use crate::settings::AppSettings;
use crate::settings::SettingsError;
#[cfg(windows)]
use crate::tui::{self, RemoteEndpoints};
#[cfg(windows)]
use crate::updater::{
    UpdateError, apply_update_and_restart, check_and_launch_update, parse_apply_update_request,
};
use crate::uri::UriError;
#[cfg(windows)]
use crate::uri::UriRequest;

#[cfg(windows)]
const FALLBACK_RELEASE_INDEX_URL: &str = "https://raw.githubusercontent.com/maynut02/astral-party-korean-patch/distribution/release-index.json";
#[cfg(windows)]
const FALLBACK_PATCHER_INDEX_URL: &str = "https://raw.githubusercontent.com/maynut02/astral-party-korean-patch/distribution/patcher-index.json";
#[cfg(windows)]
const FALLBACK_PATCHER_RELEASE_BASE_URL: &str =
    "https://github.com/maynut02/astral-party-korean-patch/releases/download";
#[derive(Debug, Error)]
pub enum CliError {
    #[error(transparent)]
    Service(#[from] ServiceError),
    #[error(transparent)]
    Settings(#[from] SettingsError),
    #[error(transparent)]
    Registration(#[from] RegistrationError),
    #[error(transparent)]
    Uri(#[from] UriError),
    #[cfg(windows)]
    #[error(transparent)]
    Update(#[from] UpdateError),
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),
    #[error("WindowsPatcher supports Windows only")]
    UnsupportedPlatform,
}

#[cfg(windows)]
fn release_index_url() -> &'static str {
    option_env!("ASTRAL_PATCH_INDEX_URL")
        .filter(|value| !value.trim().is_empty())
        .unwrap_or(FALLBACK_RELEASE_INDEX_URL)
}

#[cfg(windows)]
fn patcher_index_url() -> &'static str {
    option_env!("ASTRAL_PATCHER_INDEX_URL")
        .filter(|value| !value.trim().is_empty())
        .unwrap_or(FALLBACK_PATCHER_INDEX_URL)
}

#[cfg(windows)]
fn patcher_release_base_url() -> &'static str {
    option_env!("ASTRAL_PATCHER_RELEASE_BASE_URL")
        .filter(|value| !value.trim().is_empty())
        .unwrap_or(FALLBACK_PATCHER_RELEASE_BASE_URL)
}

#[cfg(windows)]
fn load_initial_settings(paths: &PatcherPaths) -> Result<AppSettings, CliError> {
    let mut settings = AppSettings::load(&paths.settings_path)?;
    settings.auto_detect_missing();
    // Save on startup so legacy settings are transparently rewritten as schema v3.
    settings.save(&paths.settings_path)?;
    Ok(settings)
}

#[cfg(windows)]
pub fn run() -> Result<(), CliError> {
    let paths = PatcherPaths::windows_default()?;
    let mut startup_notices = Vec::new();
    match logging::init(&paths.logs_root) {
        Ok(path) => {
            logging::info(format!(
                "WindowsPatcher v{} started; log={}",
                env!("CARGO_PKG_VERSION"),
                path.display()
            ));
            logging::info(format!("state root={}", paths.state_root.display()));
        }
        Err(error) => startup_notices.push(format!("로그 파일을 만들지 못했습니다: {error}")),
    }

    let original_args = std::env::args_os().skip(1).collect::<Vec<_>>();
    if let Some(request) = parse_apply_update_request(&original_args)? {
        logging::info(format!(
            "applying self update after process {}",
            request.previous_pid
        ));
        apply_update_and_restart(&paths.state_root, request)?;
        return Ok(());
    }

    let migration = migrate_legacy_state(&paths)?;
    if !migration.moved.is_empty() {
        for item in &migration.moved {
            logging::info(format!(
                "migrated legacy state: {} -> {}",
                item.source.display(),
                item.destination.display()
            ));
        }
        startup_notices.push(format!(
            "기존 로컬 상태를 새 저장 구조로 정리했습니다. 이동 {}개",
            migration.moved.len()
        ));
    }

    match check_and_launch_update(
        patcher_index_url(),
        patcher_release_base_url(),
        &paths.state_root,
        &original_args,
    ) {
        Ok(true) => {
            logging::info("new WindowsPatcher version downloaded; handing off to updater");
            return Ok(());
        }
        Ok(false) => logging::info("self update check: current version is up to date"),
        Err(error) => {
            logging::warn(format!("self update check failed: {error}"));
            startup_notices.push(format!(
                "자동 업데이트 확인에 실패해 현재 버전으로 계속합니다: {error}"
            ));
        }
    }

    let installed_exe = ensure_self_installed_and_registered(&paths.state_root)?;
    let mut settings = load_initial_settings(&paths)?;
    logging::info(format!(
        "settings loaded; selected Steam region={}",
        settings.selected_route().as_str()
    ));
    let initial_request = match original_args.first() {
        Some(uri) => UriRequest::parse(&uri.to_string_lossy())?,
        None => UriRequest::menu(),
    };
    if let Some(route) = initial_request.route
        && settings.auto_detect_route_missing(route)
    {
        settings.save(&paths.settings_path)?;
    }
    if std::env::current_exe()
        .ok()
        .is_some_and(|current| current != installed_exe)
    {
        startup_notices.push(format!(
            "프로그램 등록 완료: {} · 웹사이트 연동 astral://",
            installed_exe.display()
        ));
    }
    let startup_notice = (!startup_notices.is_empty()).then(|| startup_notices.join(" · "));

    logging::info("entering TUI");
    tui::run(
        paths,
        settings,
        installed_exe,
        initial_request,
        startup_notice,
        RemoteEndpoints {
            patch_release_index: release_index_url(),
        },
    )?;
    logging::info("WindowsPatcher exited normally");
    Ok(())
}

#[cfg(not(windows))]
pub fn run() -> Result<(), CliError> {
    Err(CliError::UnsupportedPlatform)
}
