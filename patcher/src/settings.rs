use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::game::{
    GameDetectError, GameInstallation, build_installation, normalize_locallow_root,
    normalize_steam_root,
};

pub const DEFAULT_CHANNEL: &str = "stable";

#[derive(Debug, Error)]
pub enum SettingsError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("settings JSON error: {0}")]
    Json(#[from] serde_json::Error),
    #[error(transparent)]
    Game(#[from] GameDetectError),
    #[error("Steam game path is not configured")]
    SteamPathMissing,
    #[error("LocalLow path is not configured")]
    LocalLowPathMissing,
    #[error("unsupported settings schema version: {0}")]
    UnsupportedSchema(u32),
    #[error("unsupported patch channel: {0}")]
    InvalidChannel(String),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AppSettings {
    pub schema_version: u32,
    pub steam_game_root: Option<PathBuf>,
    pub locallow_root: Option<PathBuf>,
    pub channel: String,
}

impl Default for AppSettings {
    fn default() -> Self {
        Self {
            schema_version: 1,
            steam_game_root: None,
            locallow_root: None,
            channel: DEFAULT_CHANNEL.into(),
        }
    }
}

impl AppSettings {
    pub fn load(path: &Path) -> Result<Self, SettingsError> {
        if !path.is_file() {
            return Ok(Self::default());
        }
        let settings: Self = serde_json::from_slice(&fs::read(path)?)?;
        settings.validate()?;
        Ok(settings)
    }

    pub fn save(&self, path: &Path) -> Result<(), SettingsError> {
        self.validate()?;
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let temp = path.with_extension("json.tmp");
        fs::write(&temp, serde_json::to_vec_pretty(self)?)?;
        if path.exists() {
            fs::remove_file(path)?;
        }
        fs::rename(temp, path)?;
        Ok(())
    }

    pub fn validate(&self) -> Result<(), SettingsError> {
        if self.schema_version != 1 {
            return Err(SettingsError::UnsupportedSchema(self.schema_version));
        }
        if !matches!(self.channel.as_str(), "stable" | "preview") {
            return Err(SettingsError::InvalidChannel(self.channel.clone()));
        }
        Ok(())
    }

    pub fn set_steam_game_root(&mut self, path: &Path) -> Result<(), SettingsError> {
        self.steam_game_root = Some(normalize_steam_root(path)?);
        Ok(())
    }

    pub fn set_locallow_root(&mut self, path: &Path) -> Result<(), SettingsError> {
        self.locallow_root = Some(normalize_locallow_root(path)?);
        Ok(())
    }

    pub fn set_channel(&mut self, channel: &str) -> Result<(), SettingsError> {
        if !matches!(channel, "stable" | "preview") {
            return Err(SettingsError::InvalidChannel(channel.into()));
        }
        self.channel = channel.into();
        Ok(())
    }

    pub fn installation(&self) -> Result<GameInstallation, SettingsError> {
        let steam = self
            .steam_game_root
            .clone()
            .ok_or(SettingsError::SteamPathMissing)?;
        let locallow = self
            .locallow_root
            .clone()
            .ok_or(SettingsError::LocalLowPathMissing)?;
        Ok(build_installation(steam, locallow)?)
    }

    #[cfg(windows)]
    pub fn auto_detect_missing(&mut self) -> bool {
        let mut changed = false;
        if self.steam_game_root.is_none()
            && let Ok(path) = crate::game::discover_windows_steam_root()
        {
            self.steam_game_root = Some(path);
            changed = true;
        }
        if self.locallow_root.is_none()
            && let Ok(path) = crate::game::discover_windows_locallow_root()
        {
            self.locallow_root = Some(path);
            changed = true;
        }
        changed
    }

    #[cfg(windows)]
    pub fn redetect_all(&mut self) -> Result<(), SettingsError> {
        let steam_game_root = crate::game::discover_windows_steam_root()?;
        let locallow_root = crate::game::discover_windows_locallow_root()?;
        self.steam_game_root = Some(steam_game_root);
        self.locallow_root = Some(locallow_root);
        Ok(())
    }

    #[cfg(not(windows))]
    pub fn auto_detect_missing(&mut self) -> bool {
        false
    }

    #[cfg(not(windows))]
    pub fn redetect_all(&mut self) -> Result<(), SettingsError> {
        Err(SettingsError::Game(GameDetectError::InstallNotFound))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn settings_round_trip() {
        let temp = tempdir().unwrap();
        let path = temp.path().join("settings.json");
        let settings = AppSettings {
            steam_game_root: Some(PathBuf::from("C:/Games/Astral Party")),
            locallow_root: Some(PathBuf::from(
                "C:/Users/Test/AppData/LocalLow/feimo/AstralParty_INT",
            )),
            channel: "preview".into(),
            ..AppSettings::default()
        };
        settings.save(&path).unwrap();
        assert_eq!(AppSettings::load(&path).unwrap(), settings);
    }

    #[test]
    fn rejects_unknown_channel() {
        let mut settings = AppSettings::default();
        assert!(settings.set_channel("nightly").is_err());
    }
}
