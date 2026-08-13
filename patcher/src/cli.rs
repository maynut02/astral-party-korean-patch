use std::io;

use thiserror::Error;

use crate::registration::RegistrationError;
#[cfg(windows)]
use crate::registration::ensure_self_installed_and_registered;
use crate::service::{PatcherPaths, ServiceError};
use crate::settings::{AppSettings, SettingsError};
use crate::tui;
use crate::uri::{UriAction, UriError};

const FALLBACK_RELEASE_INDEX_URL: &str = "https://github.com/maynut02/astral-party-korean-patch/releases/download/patch-index/release-index.json";

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
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),
    #[error("AstralAutoPatcher supports Windows only")]
    UnsupportedPlatform,
}

fn release_index_url() -> &'static str {
    option_env!("ASTRAL_PATCH_INDEX_URL")
        .filter(|value| !value.trim().is_empty())
        .unwrap_or(FALLBACK_RELEASE_INDEX_URL)
}

fn load_initial_settings(paths: &PatcherPaths) -> Result<AppSettings, CliError> {
    let mut settings = AppSettings::load(&paths.settings_path)?;
    settings.auto_detect_missing();
    // Save on startup so legacy schema v1 settings are transparently rewritten as v2.
    settings.save(&paths.settings_path)?;
    Ok(settings)
}

#[cfg(windows)]
pub fn run() -> Result<(), CliError> {
    let paths = PatcherPaths::windows_default()?;
    let installed_exe = ensure_self_installed_and_registered(&paths.state_root)?;
    let settings = load_initial_settings(&paths)?;
    let initial_action = match std::env::args().nth(1) {
        Some(uri) => UriAction::parse(&uri)?,
        None => UriAction::Menu,
    };
    let startup_notice = std::env::current_exe()
        .ok()
        .filter(|current| current != &installed_exe)
        .map(|_| {
            format!(
                "프로그램 등록 완료: {} · 웹사이트 연동 astral://",
                installed_exe.display()
            )
        });

    tui::run(
        paths,
        settings,
        installed_exe,
        initial_action,
        startup_notice,
        release_index_url(),
    )?;
    Ok(())
}

#[cfg(not(windows))]
pub fn run() -> Result<(), CliError> {
    Err(CliError::UnsupportedPlatform)
}
