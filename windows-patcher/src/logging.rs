use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const LOG_RETENTION: Duration = Duration::from_secs(7 * 24 * 60 * 60);
const MAX_LOG_BYTES: u64 = 4 * 1024 * 1024;
const LOG_PREFIX: &str = "autopatcher-";
const LOG_SUFFIX: &str = ".log";

static LOGGER: OnceLock<Mutex<FileLogger>> = OnceLock::new();

#[derive(Debug)]
struct FileLogger {
    file: File,
    bytes_written: u64,
    truncated: bool,
}

pub fn init(logs_root: &Path) -> io::Result<PathBuf> {
    fs::create_dir_all(logs_root)?;
    cleanup_old_logs(logs_root, SystemTime::now())?;
    let path = logs_root.join(format!(
        "{LOG_PREFIX}{}-{}.log",
        compact_timestamp(SystemTime::now()),
        std::process::id()
    ));
    let file = OpenOptions::new().create(true).append(true).open(&path)?;
    let bytes_written = file.metadata()?.len();
    let _ = LOGGER.set(Mutex::new(FileLogger {
        file,
        bytes_written,
        truncated: false,
    }));
    Ok(path)
}

pub fn info(message: impl AsRef<str>) {
    write("INFO", message.as_ref());
}

pub fn warn(message: impl AsRef<str>) {
    write("WARN", message.as_ref());
}

pub fn error(message: impl AsRef<str>) {
    write("ERROR", message.as_ref());
}

fn write(level: &str, message: &str) {
    let Some(logger) = LOGGER.get() else {
        return;
    };
    let Ok(mut logger) = logger.lock() else {
        return;
    };
    logger.write_entry(level, message, SystemTime::now());
}

impl FileLogger {
    fn write_entry(&mut self, level: &str, message: &str, now: SystemTime) {
        let line = format!(
            "[{}] {level} {}\n",
            display_timestamp(now),
            sanitize(message)
        );
        let line_bytes = line.as_bytes();
        if self.bytes_written.saturating_add(line_bytes.len() as u64) > MAX_LOG_BYTES {
            if !self.truncated {
                let marker = format!(
                    "[{}] WARN log size limit reached; further entries are omitted\n",
                    display_timestamp(now)
                );
                let _ = self.file.write_all(marker.as_bytes());
                let _ = self.file.flush();
                self.truncated = true;
            }
            return;
        }
        if self.file.write_all(line_bytes).is_ok() {
            self.bytes_written = self.bytes_written.saturating_add(line_bytes.len() as u64);
            let _ = self.file.flush();
        }
    }
}

fn sanitize(message: &str) -> String {
    message.replace('\r', " ").replace('\n', " | ")
}

fn cleanup_old_logs(logs_root: &Path, now: SystemTime) -> io::Result<()> {
    for entry in fs::read_dir(logs_root)? {
        let entry = entry?;
        let path = entry.path();
        if !path.is_file() || !is_log_file(&path) {
            continue;
        }
        let Ok(modified) = entry.metadata().and_then(|metadata| metadata.modified()) else {
            continue;
        };
        if now
            .duration_since(modified)
            .is_ok_and(|age| age > LOG_RETENTION)
        {
            let _ = fs::remove_file(path);
        }
    }
    Ok(())
}

fn is_log_file(path: &Path) -> bool {
    path.file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.starts_with(LOG_PREFIX) && name.ends_with(LOG_SUFFIX))
}

fn compact_timestamp(time: SystemTime) -> String {
    let (year, month, day, hour, minute, second) = utc_parts(time);
    format!("{year:04}{month:02}{day:02}-{hour:02}{minute:02}{second:02}Z")
}

fn display_timestamp(time: SystemTime) -> String {
    let (year, month, day, hour, minute, second) = utc_parts(time);
    format!("{year:04}-{month:02}-{day:02} {hour:02}:{minute:02}:{second:02}Z")
}

fn utc_parts(time: SystemTime) -> (i32, u32, u32, u32, u32, u32) {
    let seconds = time
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let days = (seconds / 86_400) as i64;
    let seconds_of_day = seconds % 86_400;
    let (year, month, day) = civil_from_days(days);
    let hour = (seconds_of_day / 3_600) as u32;
    let minute = ((seconds_of_day % 3_600) / 60) as u32;
    let second = (seconds_of_day % 60) as u32;
    (year, month, day, hour, minute, second)
}

// Howard Hinnant's civil_from_days algorithm, with day 0 = 1970-01-01.
fn civil_from_days(days_since_epoch: i64) -> (i32, u32, u32) {
    let z = days_since_epoch + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let mut year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = mp + if mp < 10 { 3 } else { -9 };
    year += if month <= 2 { 1 } else { 0 };
    (year as i32, month as u32, day as u32)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unix_epoch_formats_as_expected() {
        assert_eq!(compact_timestamp(UNIX_EPOCH), "19700101-000000Z");
        assert_eq!(display_timestamp(UNIX_EPOCH), "1970-01-01 00:00:00Z");
    }

    #[test]
    fn logger_stops_writing_after_size_limit() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("limit.log");
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .unwrap();
        let mut logger = FileLogger {
            file,
            bytes_written: MAX_LOG_BYTES - 4,
            truncated: false,
        };
        logger.write_entry("INFO", "this entry cannot fit", UNIX_EPOCH);
        assert!(logger.truncated);
        let first_size = fs::metadata(&path).unwrap().len();
        logger.write_entry("INFO", "another omitted entry", UNIX_EPOCH);
        assert_eq!(fs::metadata(&path).unwrap().len(), first_size);
    }

    #[test]
    fn cleanup_removes_only_old_autopatcher_logs() {
        let temp = tempfile::tempdir().unwrap();
        let old = temp.path().join("autopatcher-old.log");
        let keep = temp.path().join("notes.log");
        fs::write(&old, b"old").unwrap();
        fs::write(&keep, b"keep").unwrap();

        // With a far-future reference time, the freshly created AutoPatcher log is old enough.
        cleanup_old_logs(
            temp.path(),
            SystemTime::now() + LOG_RETENTION + Duration::from_secs(1),
        )
        .unwrap();
        assert!(!old.exists());
        assert!(keep.exists());
    }
}
