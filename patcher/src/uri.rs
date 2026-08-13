use thiserror::Error;

pub const URI_SCHEME: &str = "astral";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UriAction {
    Menu,
    Install,
    Remove,
    Settings,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum UriError {
    #[error("unsupported AstralAutoPatcher URI: {0}")]
    Unsupported(String),
}

impl UriAction {
    pub fn parse(value: &str) -> Result<Self, UriError> {
        let normalized = value.trim().trim_end_matches('/').to_ascii_lowercase();
        match normalized.as_str() {
            "astral:" | "astral://" => Ok(Self::Menu),
            "astral://install" => Ok(Self::Install),
            "astral://remove" => Ok(Self::Remove),
            "astral://settings" => Ok(Self::Settings),
            _ => Err(UriError::Unsupported(value.to_owned())),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_only_fixed_actions() {
        assert_eq!(
            UriAction::parse("astral://install").unwrap(),
            UriAction::Install
        );
        assert_eq!(
            UriAction::parse("ASTRAL://settings/").unwrap(),
            UriAction::Settings
        );
        assert!(UriAction::parse("astral://install?url=https://evil.test").is_err());
        assert!(UriAction::parse("https://example.test").is_err());
    }
}
