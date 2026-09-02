use std::path::{Component, Path};

use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ProtocolError {
    #[error("unsupported schema version: {0}")]
    SchemaVersion(u32),
    #[error("invalid sha256 in {0}")]
    InvalidSha256(&'static str),
    #[error("invalid catalog hash in {0}")]
    InvalidCatalogHash(&'static str),
    #[error("invalid URL in {0}: {1}")]
    InvalidUrl(&'static str, String),
    #[error("unsafe relative path: {0}")]
    UnsafePath(String),
    #[error("unsupported patch channel: {0}")]
    UnsupportedChannel(String),
    #[error("unsupported transport compression: {0}")]
    UnsupportedCompression(String),
    #[error("manifest contains no files")]
    EmptyManifest,
    #[error("manifest file has invalid size in {0}")]
    InvalidSize(&'static str),
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
    pub download_url: String,
    pub download_sha256: String,
    pub download_size: u64,
    pub compression: String,
    pub sha256: String,
    pub size: u64,
    pub source_download_url: Option<String>,
    pub source_download_sha256: Option<String>,
    pub source_download_size: Option<u64>,
    pub source_sha256: Option<String>,
    pub source_size: Option<u64>,
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

fn valid_lower_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

pub fn valid_sha256(value: &str) -> bool {
    valid_lower_hex(value, 64)
}

pub fn valid_catalog_hash(value: &str) -> bool {
    valid_lower_hex(value, 32)
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
        if self.schema_version != 2 {
            return Err(ProtocolError::SchemaVersion(self.schema_version));
        }
        if self.files.is_empty() {
            return Err(ProtocolError::EmptyManifest);
        }
        if self.patch.channel != "release" {
            return Err(ProtocolError::UnsupportedChannel(
                self.patch.channel.clone(),
            ));
        }
        if !valid_sha256(&self.patch.translation_fingerprint) {
            return Err(ProtocolError::InvalidSha256("translationFingerprint"));
        }
        if !valid_catalog_hash(&self.game.catalog_hash) {
            return Err(ProtocolError::InvalidCatalogHash("catalogHash"));
        }
        let mut seen = std::collections::HashSet::new();
        for file in &self.files {
            validate_relative_path(&file.path)?;
            if !file.download_url.starts_with("https://")
                && !file.download_url.starts_with("http://")
            {
                return Err(ProtocolError::InvalidUrl(
                    "file.downloadUrl",
                    file.download_url.clone(),
                ));
            }
            if !valid_sha256(&file.download_sha256) {
                return Err(ProtocolError::InvalidSha256("file.downloadSha256"));
            }
            if !valid_sha256(&file.sha256) {
                return Err(ProtocolError::InvalidSha256("file.sha256"));
            }
            if file.download_size == 0 {
                return Err(ProtocolError::InvalidSize("file.downloadSize"));
            }
            if file.size == 0 {
                return Err(ProtocolError::InvalidSize("file.size"));
            }
            let source_fields = [
                file.source_download_url.is_some(),
                file.source_download_sha256.is_some(),
                file.source_download_size.is_some(),
                file.source_sha256.is_some(),
                file.source_size.is_some(),
            ];
            if source_fields.iter().any(|value| *value) && source_fields.iter().any(|value| !*value)
            {
                return Err(ProtocolError::UnsafePath(
                    "incomplete source metadata".into(),
                ));
            }
            if let (
                Some(url),
                Some(download_sha256),
                Some(download_size),
                Some(source_sha256),
                Some(source_size),
            ) = (
                &file.source_download_url,
                &file.source_download_sha256,
                file.source_download_size,
                &file.source_sha256,
                file.source_size,
            ) {
                if !url.starts_with("https://") && !url.starts_with("http://") {
                    return Err(ProtocolError::InvalidUrl(
                        "file.sourceDownloadUrl",
                        url.clone(),
                    ));
                }
                if !valid_sha256(download_sha256) {
                    return Err(ProtocolError::InvalidSha256("file.sourceDownloadSha256"));
                }
                if !valid_sha256(source_sha256) {
                    return Err(ProtocolError::InvalidSha256("file.sourceSha256"));
                }
                if download_size == 0 {
                    return Err(ProtocolError::InvalidSize("file.sourceDownloadSize"));
                }
                if source_size == 0 {
                    return Err(ProtocolError::InvalidSize("file.sourceSize"));
                }
            }
            if file.compression != "gzip" {
                return Err(ProtocolError::UnsupportedCompression(
                    file.compression.clone(),
                ));
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
    pub fn validate(&self) -> Result<(), ProtocolError> {
        if self.schema_version != 1 {
            return Err(ProtocolError::SchemaVersion(self.schema_version));
        }
        for entry in &self.releases {
            if entry.route.is_empty()
                || entry.game_version.is_empty()
                || entry.revision.is_empty()
                || entry.channel.is_empty()
                || entry.patch_version.is_empty()
            {
                return Err(ProtocolError::UnsafePath("empty release field".into()));
            }
            if entry.channel != "release" {
                return Err(ProtocolError::UnsupportedChannel(entry.channel.clone()));
            }
            if !valid_catalog_hash(&entry.catalog_hash) {
                return Err(ProtocolError::InvalidCatalogHash("release.catalogHash"));
            }
            if !valid_sha256(&entry.manifest_sha256) {
                return Err(ProtocolError::InvalidSha256("release.manifestSha256"));
            }
            if !entry.manifest_url.starts_with("https://")
                && !entry.manifest_url.starts_with("http://")
            {
                return Err(ProtocolError::InvalidUrl(
                    "release.manifestUrl",
                    entry.manifest_url.clone(),
                ));
            }
        }
        Ok(())
    }

    pub fn resolve(
        &self,
        route: &str,
        game_version: &str,
        catalog_hash: &str,
        channel: &str,
    ) -> Option<&ReleaseIndexEntry> {
        if self.validate().is_err() {
            return None;
        }
        self.releases.iter().find(|entry| {
            entry.route == route
                && entry.game_version == game_version
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
            schema_version: 2,
            patch: PatchMetadata {
                version: "v1".into(),
                channel: "release".into(),
                route: "INT_STEAM".into(),
                build_id: "build-1".into(),
                translation_fingerprint: "a".repeat(64),
            },
            game: TargetGame {
                version: "3.2.0".into(),
                revision: "1042".into(),
                catalog_hash: "b".repeat(32),
            },
            files: vec![ManifestFile {
                target: InstallTarget::Addressables,
                path: "root/hash/__data".into(),
                operation: "replace".into(),
                download_url: "https://example.test/files/data.gz".into(),
                download_sha256: "d".repeat(64),
                download_size: 8,
                compression: "gzip".into(),
                sha256: "c".repeat(64),
                size: 10,
                source_download_url: Some("https://example.test/files/original.gz".into()),
                source_download_sha256: Some("e".repeat(64)),
                source_download_size: Some(9),
                source_sha256: Some("f".repeat(64)),
                source_size: Some(11),
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
    fn rejects_unsupported_distribution_channel() {
        let mut bad = manifest();
        bad.patch.channel = "preview".into();
        assert!(matches!(
            bad.validate(),
            Err(ProtocolError::UnsupportedChannel(channel)) if channel == "preview"
        ));
    }

    #[test]
    fn rejects_old_manifest_schema() {
        let mut bad = manifest();
        bad.schema_version = 1;
        assert!(matches!(
            bad.validate(),
            Err(ProtocolError::SchemaVersion(1))
        ));
    }

    #[test]
    fn rejects_partial_source_download_metadata() {
        let mut bad = manifest();
        bad.files[0].source_download_url = None;
        assert!(bad.validate().is_err());
    }

    #[test]
    fn release_resolution_requires_exact_catalog() {
        let index = ReleaseIndex {
            schema_version: 1,
            releases: vec![ReleaseIndexEntry {
                route: "INT_STEAM".into(),
                game_version: "3.2.0".into(),
                revision: "1042".into(),
                catalog_hash: "b".repeat(32),
                channel: "release".into(),
                patch_version: "v1".into(),
                manifest_url: "https://example.test/manifest.json".into(),
                manifest_sha256: "d".repeat(64),
            }],
        };
        assert!(
            index
                .resolve("INT_STEAM", "3.2.0", &"b".repeat(32), "release")
                .is_some()
        );
        assert!(
            index
                .resolve("INT_STEAM", "3.2.0", &"e".repeat(32), "release")
                .is_none()
        );
    }
}
