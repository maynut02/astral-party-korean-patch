use std::path::{Component, Path};

use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ProtocolError {
    #[error("unsupported schema version: {0}")]
    SchemaVersion(u32),
    #[error("invalid sha256 in {0}")]
    InvalidSha256(&'static str),
    #[error("unsafe relative path: {0}")]
    UnsafePath(String),
    #[error("manifest contains no files")]
    EmptyManifest,
    #[error("duplicate manifest file: {0:?}/{1}")]
    DuplicateFile(InstallTarget, String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum InstallTarget {
    Addressables,
    GameData,
}

impl InstallTarget {
    pub fn staging_dir(self) -> &'static str {
        match self {
            Self::Addressables => "addressables",
            Self::GameData => "game-data",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PatchMetadata {
    pub version: String,
    pub channel: String,
    pub route: String,
    pub build_id: String,
    pub translation_fingerprint: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TargetGame {
    pub version: String,
    pub revision: String,
    pub catalog_hash: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ManifestFile {
    pub target: InstallTarget,
    pub path: String,
    pub operation: String,
    pub sha256: String,
    pub size: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PatchManifest {
    pub schema_version: u32,
    pub patch: PatchMetadata,
    pub game: TargetGame,
    pub files: Vec<ManifestFile>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReleaseIndexEntry {
    pub route: String,
    pub game_version: String,
    pub revision: String,
    pub catalog_hash: String,
    pub channel: String,
    pub patch_version: String,
    pub manifest_url: String,
    pub manifest_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReleaseIndex {
    pub schema_version: u32,
    pub releases: Vec<ReleaseIndexEntry>,
}

pub fn valid_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

pub fn validate_relative_path(value: &str) -> Result<(), ProtocolError> {
    let path = Path::new(value);
    if value.is_empty() || path.is_absolute() {
        return Err(ProtocolError::UnsafePath(value.to_owned()));
    }
    for component in path.components() {
        match component {
            Component::Normal(_) => {}
            _ => return Err(ProtocolError::UnsafePath(value.to_owned())),
        }
    }
    Ok(())
}

impl PatchManifest {
    pub fn validate(&self) -> Result<(), ProtocolError> {
        if self.schema_version != 1 {
            return Err(ProtocolError::SchemaVersion(self.schema_version));
        }
        if self.files.is_empty() {
            return Err(ProtocolError::EmptyManifest);
        }
        if !valid_sha256(&self.patch.translation_fingerprint) {
            return Err(ProtocolError::InvalidSha256("translationFingerprint"));
        }
        if !valid_sha256(&self.game.catalog_hash) {
            return Err(ProtocolError::InvalidSha256("catalogHash"));
        }
        let mut seen = std::collections::HashSet::new();
        for file in &self.files {
            validate_relative_path(&file.path)?;
            if !valid_sha256(&file.sha256) {
                return Err(ProtocolError::InvalidSha256("file.sha256"));
            }
            if !matches!(file.operation.as_str(), "create" | "replace") {
                return Err(ProtocolError::UnsafePath(file.operation.clone()));
            }
            if !seen.insert((file.target, file.path.clone())) {
                return Err(ProtocolError::DuplicateFile(file.target, file.path.clone()));
            }
        }
        Ok(())
    }
}

impl ReleaseIndex {
    pub fn resolve(
        &self,
        route: &str,
        game_version: &str,
        revision: &str,
        catalog_hash: &str,
        channel: &str,
    ) -> Option<&ReleaseIndexEntry> {
        if self.schema_version != 1 {
            return None;
        }
        self.releases.iter().find(|entry| {
            entry.route == route
                && entry.game_version == game_version
                && entry.revision == revision
                && entry.catalog_hash == catalog_hash
                && entry.channel == channel
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn manifest() -> PatchManifest {
        PatchManifest {
            schema_version: 1,
            patch: PatchMetadata {
                version: "v1".into(),
                channel: "preview".into(),
                route: "INT_STEAM".into(),
                build_id: "build-1".into(),
                translation_fingerprint: "a".repeat(64),
            },
            game: TargetGame {
                version: "3.2.0".into(),
                revision: "1042".into(),
                catalog_hash: "b".repeat(64),
            },
            files: vec![ManifestFile {
                target: InstallTarget::Addressables,
                path: "root/hash/__data".into(),
                operation: "replace".into(),
                sha256: "c".repeat(64),
                size: 10,
            }],
        }
    }

    #[test]
    fn validates_manifest_and_rejects_traversal() {
        assert!(manifest().validate().is_ok());
        let mut bad = manifest();
        bad.files[0].path = "../escape".into();
        assert!(matches!(bad.validate(), Err(ProtocolError::UnsafePath(_))));
    }

    #[test]
    fn release_resolution_requires_exact_catalog() {
        let index = ReleaseIndex {
            schema_version: 1,
            releases: vec![ReleaseIndexEntry {
                route: "INT_STEAM".into(),
                game_version: "3.2.0".into(),
                revision: "1042".into(),
                catalog_hash: "b".repeat(64),
                channel: "stable".into(),
                patch_version: "v1".into(),
                manifest_url: "https://example.test/manifest.json".into(),
                manifest_sha256: "d".repeat(64),
            }],
        };
        assert!(
            index
                .resolve("INT_STEAM", "3.2.0", "1042", &"b".repeat(64), "stable")
                .is_some()
        );
        assert!(
            index
                .resolve("INT_STEAM", "3.2.0", "1042", &"e".repeat(64), "stable")
                .is_none()
        );
    }
}
