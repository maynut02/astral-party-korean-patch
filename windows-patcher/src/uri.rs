use thiserror::Error;

use crate::game::GameRoute;

pub const URI_SCHEME: &str = "astral";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UriAction {
    Menu,
    Install,
    Remove,
    Settings,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct UriRequest {
    pub action: UriAction,
    pub route: Option<GameRoute>,
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum UriError {
    #[error("unsupported WindowsPatcher URI: {0}")]
    Unsupported(String),
}

impl UriRequest {
    pub fn menu() -> Self {
        Self {
            action: UriAction::Menu,
            route: None,
        }
    }

    pub fn parse(value: &str) -> Result<Self, UriError> {
        let trimmed = value.trim().trim_end_matches('/');
        if trimmed.eq_ignore_ascii_case("astral:") || trimmed.eq_ignore_ascii_case("astral://") {
            return Ok(Self::menu());
        }

        let Some(prefix) = trimmed.get(..9) else {
            return Err(UriError::Unsupported(value.to_owned()));
        };
        if !prefix.eq_ignore_ascii_case("astral://") {
            return Err(UriError::Unsupported(value.to_owned()));
        }
        let rest = &trimmed[9..];
        if rest.contains(['?', '#']) {
            return Err(UriError::Unsupported(value.to_owned()));
        }

        let parts = rest.split('/').collect::<Vec<_>>();
        if parts.is_empty() || parts.len() > 2 || parts.iter().any(|part| part.is_empty()) {
            return Err(UriError::Unsupported(value.to_owned()));
        }

        let action = match parts[0].to_ascii_lowercase().as_str() {
            "install" => UriAction::Install,
            "remove" => UriAction::Remove,
            "settings" => UriAction::Settings,
            _ => return Err(UriError::Unsupported(value.to_owned())),
        };
        let route = if parts.len() == 2 {
            Some(
                GameRoute::parse(parts[1])
                    .ok_or_else(|| UriError::Unsupported(value.to_owned()))?,
            )
        } else {
            None
        };

        Ok(Self { action, route })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_legacy_actions() {
        assert_eq!(
            UriRequest::parse("astral://install").unwrap(),
            UriRequest {
                action: UriAction::Install,
                route: None,
            }
        );
        assert_eq!(
            UriRequest::parse("ASTRAL://settings/").unwrap(),
            UriRequest {
                action: UriAction::Settings,
                route: None,
            }
        );
    }

    #[test]
    fn accepts_route_scoped_actions() {
        assert_eq!(
            UriRequest::parse("astral://install/CN_STEAM").unwrap(),
            UriRequest {
                action: UriAction::Install,
                route: Some(GameRoute::CnSteam),
            }
        );
        assert_eq!(
            UriRequest::parse("astral://remove/int_steam/").unwrap(),
            UriRequest {
                action: UriAction::Remove,
                route: Some(GameRoute::IntSteam),
            }
        );
    }

    #[test]
    fn rejects_untrusted_or_unknown_uri_parts() {
        assert!(UriRequest::parse("astral://install?url=https://evil.test").is_err());
        assert!(UriRequest::parse("astral://CN_STEAM/install").is_err());
        assert!(UriRequest::parse("https://example.test").is_err());
    }
}
