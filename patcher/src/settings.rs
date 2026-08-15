use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::game::{
    GameDetectError, GameInstallation, GameRoute, build_installation, normalize_locallow_root,
    normalize_steam_root,
};

pub const SETTINGS_SCHEMA_VERSION: u32 = 3;

#[derive(Debug, Error)]
pub enum SettingsError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("settings JSON error: {0}")]
    Json(#[from] serde_json::Error),
    #[error(transparent)]
    Game(#[from] GameDetectError),
    #[error("{0} 설치 경로가 설정되어 있지 않습니다")]
    SteamPathMissing(GameRoute),
    #[error("{0} 리소스 경로가 설정되어 있지 않습니다")]
    LocalLowPathMissing(GameRoute),
    #[error("unsupported settings schema version: {0}")]
    UnsupportedSchema(u32),
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RouteSettings {
    pub steam_game_root: Option<PathBuf>,
    pub locallow_root: Option<PathBuf>,
}

impl RouteSettings {
    #[cfg(any(windows, test))]
    fn is_empty(&self) -> bool {
        self.steam_game_root.is_none() && self.locallow_root.is_none()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AppSettings {
    pub schema_version: u32,
    pub selected_route: GameRoute,
    pub routes: BTreeMap<GameRoute, RouteSettings>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct LegacySettingsV1 {
    steam_game_root: Option<PathBuf>,
    locallow_root: Option<PathBuf>,
    #[serde(default, rename = "channel")]
    _channel: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct LegacySettingsV2 {
    steam_game_root: Option<PathBuf>,
    locallow_root: Option<PathBuf>,
}

impl Default for AppSettings {
    fn default() -> Self {
        Self {
            schema_version: SETTINGS_SCHEMA_VERSION,
            selected_route: GameRoute::IntSteam,
            routes: BTreeMap::new(),
        }
    }
}

impl AppSettings {
    fn from_legacy(steam_game_root: Option<PathBuf>, locallow_root: Option<PathBuf>) -> Self {
        let mut settings = Self::default();
        if steam_game_root.is_some() || locallow_root.is_some() {
            settings.routes.insert(
                GameRoute::IntSteam,
                RouteSettings {
                    steam_game_root,
                    locallow_root,
                },
            );
        }
        settings
    }

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
                Self::from_legacy(legacy.steam_game_root, legacy.locallow_root)
            }
            2 => {
                let legacy: LegacySettingsV2 = serde_json::from_slice(&raw)?;
                Self::from_legacy(legacy.steam_game_root, legacy.locallow_root)
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

    pub fn selected_route(&self) -> GameRoute {
        self.selected_route
    }

    pub fn route_settings(&self, route: GameRoute) -> Option<&RouteSettings> {
        self.routes.get(&route)
    }

    fn route_settings_mut(&mut self, route: GameRoute) -> &mut RouteSettings {
        self.routes.entry(route).or_default()
    }

    pub fn selected_route_settings(&self) -> Option<&RouteSettings> {
        self.route_settings(self.selected_route)
    }

    pub fn steam_game_root(&self) -> Option<&Path> {
        self.selected_route_settings()
            .and_then(|settings| settings.steam_game_root.as_deref())
    }

    pub fn locallow_root(&self) -> Option<&Path> {
        self.selected_route_settings()
            .and_then(|settings| settings.locallow_root.as_deref())
    }

    pub fn set_selected_route(&mut self, route: GameRoute) {
        self.selected_route = route;
    }

    pub fn select_next_route(&mut self) {
        self.selected_route = match self.selected_route {
            GameRoute::IntSteam => GameRoute::CnSteam,
            GameRoute::CnSteam => GameRoute::IntSteam,
        };
    }

    pub fn set_steam_game_root(&mut self, path: &Path) -> Result<(), SettingsError> {
        let route = self.selected_route;
        let normalized = normalize_steam_root(path, route)?;
        self.route_settings_mut(route).steam_game_root = Some(normalized);
        Ok(())
    }

    pub fn set_locallow_root(&mut self, path: &Path) -> Result<(), SettingsError> {
        let route = self.selected_route;
        let normalized = normalize_locallow_root(path, route)?;
        self.route_settings_mut(route).locallow_root = Some(normalized);
        Ok(())
    }

    pub fn installation_for(&self, route: GameRoute) -> Result<GameInstallation, SettingsError> {
        let settings = self.route_settings(route);
        let steam = settings
            .and_then(|value| value.steam_game_root.clone())
            .ok_or(SettingsError::SteamPathMissing(route))?;
        let locallow = settings
            .and_then(|value| value.locallow_root.clone())
            .ok_or(SettingsError::LocalLowPathMissing(route))?;
        Ok(build_installation(route, steam, locallow)?)
    }

    pub fn installation(&self) -> Result<GameInstallation, SettingsError> {
        self.installation_for(self.selected_route)
    }

    #[cfg(any(windows, test))]
    fn select_only_detected_route_if_unconfigured(&mut self, detected: &[GameRoute]) -> bool {
        let current_empty = self
            .route_settings(self.selected_route)
            .is_none_or(RouteSettings::is_empty);
        if !current_empty {
            return false;
        }
        if detected.len() == 1 && detected[0] != self.selected_route {
            self.selected_route = detected[0];
            return true;
        }
        false
    }

    #[cfg(windows)]
    pub fn auto_detect_route_missing(&mut self, route: GameRoute) -> bool {
        let mut changed = false;
        let needs_steam = self
            .route_settings(route)
            .and_then(|settings| settings.steam_game_root.as_ref())
            .is_none();
        if needs_steam && let Ok(path) = crate::game::discover_windows_steam_root(route) {
            self.route_settings_mut(route).steam_game_root = Some(path);
            changed = true;
        }
        let needs_locallow = self
            .route_settings(route)
            .and_then(|settings| settings.locallow_root.as_ref())
            .is_none();
        if needs_locallow && let Ok(path) = crate::game::discover_windows_locallow_root(route) {
            self.route_settings_mut(route).locallow_root = Some(path);
            changed = true;
        }
        changed
    }

    #[cfg(windows)]
    pub fn auto_detect_selected_missing(&mut self) -> bool {
        self.auto_detect_route_missing(self.selected_route)
    }

    #[cfg(windows)]
    pub fn auto_detect_missing(&mut self) -> bool {
        let detected = crate::game::detect_windows_routes();
        let mut changed = self.select_only_detected_route_if_unconfigured(&detected);
        changed |= self.auto_detect_selected_missing();
        changed
    }

    #[cfg(windows)]
    pub fn redetect_all(&mut self) -> Result<(), SettingsError> {
        let route = self.selected_route;
        let steam_game_root = crate::game::discover_windows_steam_root(route)?;
        let locallow_root = crate::game::discover_windows_locallow_root(route)?;
        self.routes.insert(
            route,
            RouteSettings {
                steam_game_root: Some(steam_game_root),
                locallow_root: Some(locallow_root),
            },
        );
        Ok(())
    }

    #[cfg(not(windows))]
    pub fn auto_detect_route_missing(&mut self, _route: GameRoute) -> bool {
        false
    }

    #[cfg(not(windows))]
    pub fn auto_detect_selected_missing(&mut self) -> bool {
        false
    }

    #[cfg(not(windows))]
    pub fn auto_detect_missing(&mut self) -> bool {
        false
    }

    #[cfg(not(windows))]
    pub fn redetect_all(&mut self) -> Result<(), SettingsError> {
        Err(SettingsError::Game(GameDetectError::InstallNotFound(
            self.selected_route,
        )))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn settings_round_trip_preserves_route_specific_paths() {
        let temp = tempdir().unwrap();
        let path = temp.path().join("settings.json");
        let mut settings = AppSettings {
            selected_route: GameRoute::CnSteam,
            ..AppSettings::default()
        };
        settings.routes.insert(
            GameRoute::IntSteam,
            RouteSettings {
                steam_game_root: Some(PathBuf::from("C:/Games/Astral Party")),
                locallow_root: Some(PathBuf::from(
                    "C:/Users/Test/AppData/LocalLow/feimo/AstralParty_INT",
                )),
            },
        );
        settings.routes.insert(
            GameRoute::CnSteam,
            RouteSettings {
                steam_game_root: Some(PathBuf::from("D:/Games/Astral Party")),
                locallow_root: Some(PathBuf::from(
                    "C:/Users/Test/AppData/LocalLow/feimo/AstralParty_CN",
                )),
            },
        );
        settings.save(&path).unwrap();
        assert_eq!(AppSettings::load(&path).unwrap(), settings);
    }

    #[test]
    fn migrates_v1_settings_and_drops_channel_into_int_route() {
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
        assert_eq!(settings.selected_route, GameRoute::IntSteam);
        let int = settings.route_settings(GameRoute::IntSteam).unwrap();
        assert_eq!(
            int.steam_game_root,
            Some(PathBuf::from("C:/Games/Astral Party"))
        );
        assert_eq!(
            int.locallow_root,
            Some(PathBuf::from(
                "C:/Users/Test/AppData/LocalLow/feimo/AstralParty_INT"
            ))
        );

        settings.save(&path).unwrap();
        let saved = fs::read_to_string(&path).unwrap();
        assert!(!saved.contains("channel"));
        assert!(saved.contains("selectedRoute"));
        assert!(saved.contains("INT_STEAM"));
    }

    #[test]
    fn migrates_v2_settings_into_int_route() {
        let temp = tempdir().unwrap();
        let path = temp.path().join("settings.json");
        fs::write(
            &path,
            br#"{
  "schemaVersion": 2,
  "steamGameRoot": "C:/Games/Astral Party",
  "locallowRoot": "C:/Users/Test/AppData/LocalLow/feimo/AstralParty_INT"
}"#,
        )
        .unwrap();
        let settings = AppSettings::load(&path).unwrap();
        assert_eq!(settings.selected_route, GameRoute::IntSteam);
        assert!(settings.route_settings(GameRoute::IntSteam).is_some());
    }

    #[test]
    fn startup_selects_cn_when_cn_is_the_only_detected_route() {
        let mut settings = AppSettings::default();
        assert!(settings.select_only_detected_route_if_unconfigured(&[GameRoute::CnSteam]));
        assert_eq!(settings.selected_route(), GameRoute::CnSteam);
    }

    #[test]
    fn startup_keeps_current_route_when_both_routes_are_detected() {
        let mut settings = AppSettings::default();
        assert!(!settings.select_only_detected_route_if_unconfigured(&GameRoute::ALL));
        assert_eq!(settings.selected_route(), GameRoute::IntSteam);

        settings.set_selected_route(GameRoute::CnSteam);
        assert!(!settings.select_only_detected_route_if_unconfigured(&GameRoute::ALL));
        assert_eq!(settings.selected_route(), GameRoute::CnSteam);
    }

    #[test]
    fn configured_route_is_not_auto_switched() {
        let mut settings = AppSettings::default();
        settings.routes.insert(
            GameRoute::IntSteam,
            RouteSettings {
                steam_game_root: Some(PathBuf::from("C:/INT")),
                locallow_root: None,
            },
        );
        assert!(!settings.select_only_detected_route_if_unconfigured(&[GameRoute::CnSteam]));
        assert_eq!(settings.selected_route(), GameRoute::IntSteam);
    }
    #[test]
    fn route_selection_keeps_each_routes_paths() {
        let mut settings = AppSettings::default();
        settings.routes.insert(
            GameRoute::IntSteam,
            RouteSettings {
                steam_game_root: Some(PathBuf::from("C:/INT")),
                locallow_root: Some(PathBuf::from("C:/INT_LOCAL")),
            },
        );
        settings.routes.insert(
            GameRoute::CnSteam,
            RouteSettings {
                steam_game_root: Some(PathBuf::from("C:/CN")),
                locallow_root: Some(PathBuf::from("C:/CN_LOCAL")),
            },
        );
        assert_eq!(settings.steam_game_root(), Some(Path::new("C:/INT")));
        settings.select_next_route();
        assert_eq!(settings.selected_route, GameRoute::CnSteam);
        assert_eq!(settings.steam_game_root(), Some(Path::new("C:/CN")));
    }
}
