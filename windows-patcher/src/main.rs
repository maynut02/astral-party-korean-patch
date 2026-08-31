fn main() {
    if let Err(error) = astral_auto_patcher::cli::run() {
        astral_auto_patcher::logging::error(format!("fatal error: {error}"));
        eprintln!("WindowsPatcher 오류: {error}");
        std::process::exit(1);
    }
}
