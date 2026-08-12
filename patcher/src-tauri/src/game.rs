use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

pub const STEAM_APP_ID: &str = "2622000";
pub const DEFAULT_INSTALL_DIR: &str = "Astral Party";
pub const LOCALLOW_RELATIVE: &str = "AppData/LocalLow/feimo/AstralParty_INT/com.unity.addressables";

#[derive(Debug, Error)]
pub enum GameDetectError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("Steam manifest is malformed: {0}")]
    Manifest(String),
    #[error("Astral Party Steam manifest was not found")]
    ManifestNotFound,
    #[error("Astral Party installation directory was not found")]
    InstallNotFound,
    #[error("Addressables catalog hash was not found")]
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
    pub game_root: PathBuf,
    pub game_data_root: PathBuf,
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

pub fn find_install_from_libraries(libraries: &[PathBuf]) -> Result<PathBuf, GameDetectError> {
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
        if game_root.is_dir() {
            return Ok(game_root);
        }
    }
    Err(GameDetectError::InstallNotFound)
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

pub fn build_installation(
    game_root: PathBuf,
    user_profile: &Path,
) -> Result<GameInstallation, GameDetectError> {
    let executable_root = game_root.join("8vJXnINT");
    let game_data_root = executable_root.join("AstralParty_INT_Data");
    if !game_data_root.is_dir() {
        return Err(GameDetectError::InstallNotFound);
    }
    let addressables_root = user_profile.join(LOCALLOW_RELATIVE);
    if !addressables_root.is_dir() {
        return Err(GameDetectError::CatalogNotFound);
    }
    let catalog = discover_latest_catalog(&addressables_root)?;
    Ok(GameInstallation {
        game_root: executable_root,
        game_data_root,
        addressables_root,
        catalog,
    })
}

#[cfg(windows)]
pub fn discover_windows_installation() -> Result<GameInstallation, GameDetectError> {
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

    let game_root = find_install_from_libraries(&libraries)?;
    let user_profile = std::env::var_os("USERPROFILE")
        .map(PathBuf::from)
        .ok_or(GameDetectError::CatalogNotFound)?;
    build_installation(game_root, &user_profile)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

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
    fn builds_installation_from_fixture_layout() {
        let temp = tempdir().unwrap();
        let game_root = temp.path().join("Astral Party");
        fs::create_dir_all(game_root.join("8vJXnINT/AstralParty_INT_Data")).unwrap();
        let user = temp.path().join("user");
        let addressables = user.join(LOCALLOW_RELATIVE);
        fs::create_dir_all(&addressables).unwrap();
        fs::write(addressables.join("catalog_3.2.0.hash"), "fd58").unwrap();

        let install = build_installation(game_root, &user).unwrap();
        assert!(install.game_root.ends_with("8vJXnINT"));
        assert_eq!(install.catalog.version, "3.2.0");
    }
}
