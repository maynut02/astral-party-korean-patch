use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::game::{
    GameDetectError, GameInstallation, build_installation, normalize_locallow_root,
    normalize_steam_root,
};

pub const SETTINGS_SCHEMA_VERSION: u32 = 2;

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
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AppSettings {
    pub schema_version: u32,
    pub steam_game_root: Option<PathBuf>,
    pub locallow_root: Option<PathBuf>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct LegacySettingsV1 {
    steam_game_root: Option<PathBuf>,
    locallow_root: Option<PathBuf>,
    #[serde(default, rename = "channel")]
    _channel: Option<String>,
}

impl Default for AppSettings {
    fn default() -> Self {
        Self {
            schema_version: SETTINGS_SCHEMA_VERSION,
            steam_game_root: None,
            locallow_root: None,
        }
    }
}

impl AppSettings {
    pub fn load(path: &Path) -> Result<Self, SettingsError> {
        if !path.is_file() {
            return Ok(Self::default());
        }
        let raw = fs::read(path)?;
        let value: serde_json::Value = serde_json::from_slice(&raw)?;
        let schema_version = value
            .get("schemaVersion")
            .and_then(serde_json::Value::as_u64)
            .unwrap_or(0) as u32;
        let settings = match schema_version {
            1 => {
                let legacy: LegacySettingsV1 = serde_json::from_slice(&raw)?;
                Self {
                    schema_version: SETTINGS_SCHEMA_VERSION,
                    steam_game_root: legacy.steam_game_root,
                    locallow_root: legacy.locallow_root,
                }
            }
            SETTINGS_SCHEMA_VERSION => serde_json::from_slice(&raw)?,
            other => return Err(SettingsError::UnsupportedSchema(other)),
        };
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
        if self.schema_version != SETTINGS_SCHEMA_VERSION {
            return Err(SettingsError::UnsupportedSchema(self.schema_version));
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
            ..AppSettings::default()
        };
        settings.save(&path).unwrap();
        assert_eq!(AppSettings::load(&path).unwrap(), settings);
    }

    #[test]
    fn migrates_v1_settings_and_drops_channel() {
        let temp = tempdir().unwrap();
        let path = temp.path().join("settings.json");
        fs::write(
            &path,
            br#"{
  "schemaVersion": 1,
  "steamGameRoot": "C:/Games/Astral Party",
  "locallowRoot": "C:/Users/Test/AppData/LocalLow/feimo/AstralParty_INT",
  "channel": "preview"
}"#,
        )
        .unwrap();

        let settings = AppSettings::load(&path).unwrap();
        assert_eq!(settings.schema_version, SETTINGS_SCHEMA_VERSION);
        assert_eq!(
            settings.steam_game_root,
            Some(PathBuf::from("C:/Games/Astral Party"))
        );
        assert_eq!(
            settings.locallow_root,
            Some(PathBuf::from(
                "C:/Users/Test/AppData/LocalLow/feimo/AstralParty_INT"
            ))
        );

        settings.save(&path).unwrap();
        let saved = fs::read_to_string(&path).unwrap();
        assert!(!saved.contains("channel"));
    }
}
