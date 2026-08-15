use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

pub const STEAM_APP_ID: &str = "2622000";
pub const ADDRESSABLES_DIR: &str = "com.unity.addressables";

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum GameRoute {
    IntSteam,
    CnSteam,
}

impl GameRoute {
    pub const ALL: [Self; 2] = [Self::IntSteam, Self::CnSteam];

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::IntSteam => "INT_STEAM",
            Self::CnSteam => "CN_STEAM",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value.trim().to_ascii_uppercase().as_str() {
            "INT_STEAM" => Some(Self::IntSteam),
            "CN_STEAM" => Some(Self::CnSteam),
            _ => None,
        }
    }

    pub const fn display_name(self) -> &'static str {
        match self {
            Self::IntSteam => "Steam 글로벌",
            Self::CnSteam => "Steam 중국",
        }
    }

    pub const fn slug(self) -> &'static str {
        match self {
            Self::IntSteam => "int-steam",
            Self::CnSteam => "cn-steam",
        }
    }

    pub const fn executable_dir(self) -> &'static str {
        match self {
            Self::IntSteam => "8vJXnINT",
            Self::CnSteam => "8vJXn6CN",
        }
    }

    pub const fn data_dir(self) -> &'static str {
        match self {
            Self::IntSteam => "AstralParty_INT_Data",
            Self::CnSteam => "AstralParty_CN_Data",
        }
    }

    pub const fn locallow_dir(self) -> &'static str {
        match self {
            Self::IntSteam => "AstralParty_INT",
            Self::CnSteam => "AstralParty_CN",
        }
    }

    pub fn locallow_relative(self) -> PathBuf {
        PathBuf::from("AppData")
            .join("LocalLow")
            .join("feimo")
            .join(self.locallow_dir())
    }
}

impl std::fmt::Display for GameRoute {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.display_name())
    }
}

#[derive(Debug, Error)]
pub enum GameDetectError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("Steam manifest is malformed: {0}")]
    Manifest(String),
    #[error("Astral Party Steam manifest was not found")]
    ManifestNotFound,
    #[error("Astral Party {0} 설치 경로를 찾을 수 없거나 올바르지 않습니다")]
    InstallNotFound(GameRoute),
    #[error("Astral Party {0} 리소스 경로를 찾을 수 없거나 올바르지 않습니다")]
    LocalLowNotFound(GameRoute),
    #[error("게임 리소스 버전 정보를 찾을 수 없습니다")]
    CatalogNotFound,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CatalogIdentity {
    pub version: String,
    pub hash: String,
    pub hash_path: PathBuf,
    pub catalog_path: Option<PathBuf>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GameInstallation {
    pub route: GameRoute,
    /// Steam's `.../steamapps/common/Astral Party` directory.
    pub game_root: PathBuf,
    pub game_data_root: PathBuf,
    /// `%USERPROFILE%/AppData/LocalLow/feimo/AstralParty_{INT|CN}`.
    pub locallow_root: PathBuf,
    pub addressables_root: PathBuf,
    pub catalog: CatalogIdentity,
}

pub fn parse_install_dir_from_acf(text: &str) -> Result<String, GameDetectError> {
    for line in text.lines() {
        let trimmed = line.trim();
        if !trimmed.starts_with('"') {
            continue;
        }
        let parts: Vec<_> = trimmed.split('"').collect();
        if parts.len() >= 4 && parts[1] == "installdir" {
            let value = parts[3].trim();
            if value.is_empty() {
                return Err(GameDetectError::Manifest("empty installdir".into()));
            }
            return Ok(value.to_string());
        }
    }
    Err(GameDetectError::Manifest("missing installdir".into()))
}

pub fn parse_library_folders_vdf(text: &str) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    for line in text.lines() {
        let parts: Vec<_> = line.split('"').collect();
        for index in (1..parts.len()).step_by(2) {
            if parts[index] != "path" || index + 2 >= parts.len() {
                continue;
            }
            let raw = parts[index + 2].replace("\\\\", "\\");
            if !raw.is_empty() {
                roots.push(PathBuf::from(raw));
            }
        }
    }
    roots
}

pub fn find_install_from_libraries(
    libraries: &[PathBuf],
    route: GameRoute,
) -> Result<PathBuf, GameDetectError> {
    for library in libraries {
        let manifest = library
            .join("steamapps")
            .join(format!("appmanifest_{STEAM_APP_ID}.acf"));
        if !manifest.is_file() {
            continue;
        }
        let manifest_text = fs::read_to_string(&manifest)?;
        let install_dir = parse_install_dir_from_acf(&manifest_text)?;
        let game_root = library.join("steamapps").join("common").join(install_dir);
        if normalize_steam_root(&game_root, route).is_ok() {
            return Ok(game_root);
        }
    }
    Err(GameDetectError::InstallNotFound(route))
}

fn version_key(value: &str) -> Vec<u64> {
    value
        .split('.')
        .map(|part| part.parse::<u64>().unwrap_or(0))
        .collect()
}

pub fn discover_latest_catalog(
    addressables_root: &Path,
) -> Result<CatalogIdentity, GameDetectError> {
    let mut candidates = Vec::new();
    for entry in fs::read_dir(addressables_root)? {
        let entry = entry?;
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let Some(name) = path.file_name().and_then(|v| v.to_str()) else {
            continue;
        };
        let Some(version) = name
            .strip_prefix("catalog_")
            .and_then(|v| v.strip_suffix(".hash"))
        else {
            continue;
        };
        let hash = fs::read_to_string(&path)?.trim().to_ascii_lowercase();
        if hash.is_empty() || !hash.chars().all(|ch| ch.is_ascii_hexdigit()) {
            continue;
        }
        let catalog_path = addressables_root.join(format!("catalog_{version}.json"));
        candidates.push(CatalogIdentity {
            version: version.to_string(),
            hash,
            hash_path: path,
            catalog_path: catalog_path.is_file().then_some(catalog_path),
        });
    }
    candidates
        .into_iter()
        .max_by_key(|item| version_key(&item.version))
        .ok_or(GameDetectError::CatalogNotFound)
}

pub fn normalize_steam_root(path: &Path, route: GameRoute) -> Result<PathBuf, GameDetectError> {
    let executable = route.executable_dir();
    let data = route.data_dir();

    if path.join(executable).join(data).is_dir() {
        return Ok(path.to_owned());
    }
    if path.join(data).is_dir()
        && path
            .file_name()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.eq_ignore_ascii_case(executable))
    {
        return path
            .parent()
            .map(Path::to_owned)
            .ok_or(GameDetectError::InstallNotFound(route));
    }
    if path.is_dir()
        && path
            .file_name()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.eq_ignore_ascii_case(data))
    {
        return path
            .parent()
            .and_then(Path::parent)
            .map(Path::to_owned)
            .ok_or(GameDetectError::InstallNotFound(route));
    }
    Err(GameDetectError::InstallNotFound(route))
}

pub fn normalize_locallow_root(path: &Path, route: GameRoute) -> Result<PathBuf, GameDetectError> {
    if path.join(ADDRESSABLES_DIR).is_dir()
        && path
            .file_name()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.eq_ignore_ascii_case(route.locallow_dir()))
    {
        return Ok(path.to_owned());
    }
    if path.is_dir()
        && path
            .file_name()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.eq_ignore_ascii_case(ADDRESSABLES_DIR))
    {
        let parent = path
            .parent()
            .ok_or(GameDetectError::LocalLowNotFound(route))?;
        if parent
            .file_name()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.eq_ignore_ascii_case(route.locallow_dir()))
        {
            return Ok(parent.to_owned());
        }
    }
    Err(GameDetectError::LocalLowNotFound(route))
}

pub fn build_installation(
    route: GameRoute,
    steam_root: PathBuf,
    locallow_root: PathBuf,
) -> Result<GameInstallation, GameDetectError> {
    let game_root = normalize_steam_root(&steam_root, route)?;
    let locallow_root = normalize_locallow_root(&locallow_root, route)?;
    let game_data_root = game_root
        .join(route.executable_dir())
        .join(route.data_dir());
    let addressables_root = locallow_root.join(ADDRESSABLES_DIR);
    let catalog = discover_latest_catalog(&addressables_root)?;
    Ok(GameInstallation {
        route,
        game_root,
        game_data_root,
        locallow_root,
        addressables_root,
        catalog,
    })
}

#[cfg(windows)]
fn steam_libraries() -> Result<Vec<PathBuf>, GameDetectError> {
    use winreg::RegKey;
    use winreg::enums::HKEY_CURRENT_USER;

    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let steam_key = hkcu
        .open_subkey("Software\\Valve\\Steam")
        .map_err(|_| GameDetectError::ManifestNotFound)?;
    let steam_path: String = steam_key
        .get_value("SteamPath")
        .map_err(|_| GameDetectError::ManifestNotFound)?;
    let steam_root = PathBuf::from(steam_path.replace('/', "\\"));

    let mut libraries = vec![steam_root.clone()];
    let library_vdf = steam_root.join("steamapps").join("libraryfolders.vdf");
    if library_vdf.is_file() {
        let text = fs::read_to_string(library_vdf)?;
        for path in parse_library_folders_vdf(&text) {
            if !libraries.contains(&path) {
                libraries.push(path);
            }
        }
    }
    Ok(libraries)
}

#[cfg(windows)]
pub fn discover_windows_steam_root(route: GameRoute) -> Result<PathBuf, GameDetectError> {
    find_install_from_libraries(&steam_libraries()?, route)
        .and_then(|path| normalize_steam_root(&path, route))
}

#[cfg(windows)]
pub fn discover_windows_locallow_root(route: GameRoute) -> Result<PathBuf, GameDetectError> {
    let user_profile = std::env::var_os("USERPROFILE")
        .map(PathBuf::from)
        .ok_or(GameDetectError::LocalLowNotFound(route))?;
    normalize_locallow_root(&user_profile.join(route.locallow_relative()), route)
}

#[cfg(windows)]
pub fn discover_windows_installation(
    route: GameRoute,
) -> Result<GameInstallation, GameDetectError> {
    build_installation(
        route,
        discover_windows_steam_root(route)?,
        discover_windows_locallow_root(route)?,
    )
}

#[cfg(windows)]
pub fn detect_windows_routes() -> Vec<GameRoute> {
    GameRoute::ALL
        .into_iter()
        .filter(|route| discover_windows_steam_root(*route).is_ok())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn make_route_tree(root: &Path, route: GameRoute) -> (PathBuf, PathBuf) {
        let game_root = root.join("Astral Party");
        fs::create_dir_all(
            game_root
                .join(route.executable_dir())
                .join(route.data_dir()),
        )
        .unwrap();
        let locallow = root.join(route.locallow_dir());
        let addressables = locallow.join(ADDRESSABLES_DIR);
        fs::create_dir_all(&addressables).unwrap();
        fs::write(addressables.join("catalog_3.2.0.hash"), "fd58").unwrap();
        (game_root, locallow)
    }

    #[test]
    fn route_metadata_matches_known_layout() {
        assert_eq!(GameRoute::IntSteam.executable_dir(), "8vJXnINT");
        assert_eq!(GameRoute::IntSteam.data_dir(), "AstralParty_INT_Data");
        assert_eq!(GameRoute::CnSteam.executable_dir(), "8vJXn6CN");
        assert_eq!(GameRoute::CnSteam.data_dir(), "AstralParty_CN_Data");
        assert_eq!(GameRoute::CnSteam.locallow_dir(), "AstralParty_CN");
        assert_eq!(GameRoute::IntSteam.display_name(), "Steam 글로벌");
        assert_eq!(GameRoute::CnSteam.display_name(), "Steam 중국");
    }

    #[test]
    fn parses_manifest_install_dir() {
        let acf = r#""AppState"
{
    "appid" "2622000"
    "installdir" "Astral Party"
}"#;
        assert_eq!(parse_install_dir_from_acf(acf).unwrap(), "Astral Party");
    }

    #[test]
    fn parses_library_folder_paths() {
        let vdf = r#""libraryfolders"
{
  "0" { "path" "C:\\Program Files (x86)\\Steam" }
  "1" { "path" "D:\\SteamLibrary" }
}"#;
        let paths = parse_library_folders_vdf(vdf);
        assert_eq!(paths.len(), 2);
        assert!(paths[1].to_string_lossy().contains("SteamLibrary"));
    }

    #[test]
    fn selects_latest_semantic_catalog_version() {
        let temp = tempdir().unwrap();
        fs::write(temp.path().join("catalog_3.9.0.hash"), "aaa").unwrap();
        fs::write(temp.path().join("catalog_3.10.0.hash"), "bbb").unwrap();
        fs::write(temp.path().join("catalog_3.10.0.json"), "{}").unwrap();

        let latest = discover_latest_catalog(temp.path()).unwrap();
        assert_eq!(latest.version, "3.10.0");
        assert_eq!(latest.hash, "bbb");
        assert!(latest.catalog_path.is_some());
    }

    #[test]
    fn builds_installation_for_each_steam_route() {
        for route in GameRoute::ALL {
            let temp = tempdir().unwrap();
            let (game_root, locallow) = make_route_tree(temp.path(), route);
            let install = build_installation(route, game_root.clone(), locallow.clone()).unwrap();
            assert_eq!(install.route, route);
            assert_eq!(install.game_root, game_root);
            assert_eq!(install.locallow_root, locallow);
            assert_eq!(
                install.game_data_root,
                game_root
                    .join(route.executable_dir())
                    .join(route.data_dir())
            );
            assert_eq!(install.catalog.version, "3.2.0");
        }
    }

    #[test]
    fn accepts_inner_manual_paths_and_normalizes_them() {
        for route in GameRoute::ALL {
            let temp = tempdir().unwrap();
            let (game_root, locallow) = make_route_tree(temp.path(), route);
            let executable_root = game_root.join(route.executable_dir());
            let data_root = executable_root.join(route.data_dir());
            let addressables = locallow.join(ADDRESSABLES_DIR);

            assert_eq!(
                normalize_steam_root(&executable_root, route).unwrap(),
                game_root
            );
            assert_eq!(normalize_steam_root(&data_root, route).unwrap(), game_root);
            assert_eq!(
                normalize_locallow_root(&addressables, route).unwrap(),
                locallow
            );
        }
    }

    #[test]
    fn rejects_paths_from_the_other_route() {
        let temp = tempdir().unwrap();
        let (game_root, locallow) = make_route_tree(temp.path(), GameRoute::IntSteam);
        assert!(normalize_steam_root(&game_root, GameRoute::CnSteam).is_err());
        assert!(normalize_locallow_root(&locallow, GameRoute::CnSteam).is_err());
    }
}
