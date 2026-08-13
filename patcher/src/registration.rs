#[cfg(windows)]
use std::fs;
use std::path::{Path, PathBuf};

use thiserror::Error;

pub const APP_NAME: &str = "AstralAutoPatcher";
pub const EXE_NAME: &str = "AstralAutoPatcher.exe";
pub const PROTOCOL_SCHEME: &str = crate::uri::URI_SCHEME;

#[derive(Debug, Error)]
pub enum RegistrationError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
}

pub fn installed_exe_path(state_root: &Path) -> PathBuf {
    state_root.join(EXE_NAME)
}

pub fn protocol_command(exe: &Path) -> String {
    format!("\"{}\" \"%1\"", exe.display())
}

#[cfg(windows)]
fn same_file(left: &Path, right: &Path) -> bool {
    let Ok(left) = fs::canonicalize(left) else {
        return false;
    };
    let Ok(right) = fs::canonicalize(right) else {
        return false;
    };
    left.to_string_lossy()
        .eq_ignore_ascii_case(&right.to_string_lossy())
}

#[cfg(windows)]
pub fn ensure_self_installed_and_registered(
    state_root: &Path,
) -> Result<PathBuf, RegistrationError> {
    use winreg::RegKey;
    use winreg::enums::HKEY_CURRENT_USER;

    fs::create_dir_all(state_root)?;
    let current = std::env::current_exe()?;
    let installed = installed_exe_path(state_root);
    if !same_file(&current, &installed) {
        let temp = state_root.join("AstralAutoPatcher.update.tmp.exe");
        let _ = fs::remove_file(&temp);
        fs::copy(&current, &temp)?;
        if installed.exists() {
            fs::remove_file(&installed)?;
        }
        fs::rename(&temp, &installed)?;
    }

    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let base = format!("Software\\Classes\\{PROTOCOL_SCHEME}");
    let (protocol, _) = hkcu.create_subkey(&base)?;
    protocol.set_value("", &format!("URL:{APP_NAME} Protocol"))?;
    protocol.set_value("URL Protocol", &"")?;

    let (icon, _) = protocol.create_subkey("DefaultIcon")?;
    icon.set_value("", &format!("\"{}\",0", installed.display()))?;

    let (command, _) = protocol.create_subkey("shell\\open\\command")?;
    command.set_value("", &protocol_command(&installed))?;
    Ok(installed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn protocol_command_quotes_executable_and_uri() {
        let command = protocol_command(Path::new("C:/Users/Test/App Data/AstralAutoPatcher.exe"));
        assert_eq!(
            command,
            "\"C:/Users/Test/App Data/AstralAutoPatcher.exe\" \"%1\""
        );
    }
}
