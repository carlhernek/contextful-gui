//! Cross-platform prerequisite detection and (Windows) auto-install (spec section 11).

use std::net::ToSocketAddrs;
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

use serde::Serialize;

/// Silent command builder — no console flash on Windows.
pub fn silent_command<S: AsRef<std::ffi::OsStr>>(program: S) -> Command {
    let cmd = Command::new(program);
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let mut cmd = cmd;
        cmd.creation_flags(CREATE_NO_WINDOW);
        return cmd;
    }
    #[cfg(not(target_os = "windows"))]
    cmd
}

#[derive(Debug, Clone, Serialize)]
pub struct PrereqStatus {
    pub git: bool,
    pub git_path: Option<String>,
    pub python: bool,
    pub python_path: Option<String>,
    pub ripgrep: bool,
    pub network: bool,
}

fn runs_ok(path: &str, args: &[&str]) -> bool {
    silent_command(path)
        .args(args)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

fn first_working(candidates: &[PathBuf], args: &[&str]) -> Option<String> {
    for cand in candidates {
        let p = cand.to_string_lossy().to_string();
        if (cand.exists() || which(&p).is_some()) && runs_ok(&p, args) {
            return Some(p);
        }
    }
    None
}

fn which(program: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    let exts: Vec<String> = if cfg!(windows) {
        std::env::var("PATHEXT")
            .unwrap_or_else(|_| ".EXE;.CMD;.BAT".into())
            .split(';')
            .map(|s| s.to_string())
            .collect()
    } else {
        vec![String::new()]
    };
    for dir in std::env::split_paths(&path) {
        for ext in &exts {
            let candidate = dir.join(format!("{program}{ext}"));
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

fn detect_git() -> Option<String> {
    let mut candidates = vec![PathBuf::from("git")];
    #[cfg(target_os = "windows")]
    {
        candidates.push(PathBuf::from(r"C:\Program Files\Git\cmd\git.exe"));
        if let Some(local) = std::env::var_os("LOCALAPPDATA") {
            candidates.push(PathBuf::from(local).join(r"Programs\Git\cmd\git.exe"));
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        candidates.push(PathBuf::from("/usr/bin/git"));
        candidates.push(PathBuf::from("/usr/local/bin/git"));
        candidates.push(PathBuf::from("/opt/homebrew/bin/git"));
    }
    first_working(&candidates, &["--version"])
}

fn detect_python() -> Option<String> {
    let mut candidates = vec![PathBuf::from("python3"), PathBuf::from("python")];
    #[cfg(target_os = "windows")]
    {
        candidates.push(PathBuf::from("py")); // launcher
        for v in ["312", "313", "311"] {
            candidates.push(PathBuf::from(format!(r"C:\Program Files\Python{v}\python.exe")));
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        candidates.push(PathBuf::from("/usr/bin/python3"));
        candidates.push(PathBuf::from("/usr/local/bin/python3"));
        candidates.push(PathBuf::from("/opt/homebrew/bin/python3"));
    }
    // Verify it is a real Python 3 (avoids the Windows Store alias shim).
    for cand in &candidates {
        let p = cand.to_string_lossy().to_string();
        if !(cand.exists() || which(&p).is_some()) {
            continue;
        }
        if let Ok(out) = silent_command(&p).arg("--version").output() {
            let text = format!(
                "{}{}",
                String::from_utf8_lossy(&out.stdout),
                String::from_utf8_lossy(&out.stderr)
            );
            if out.status.success() && text.trim().starts_with("Python 3") {
                return Some(p);
            }
        }
    }
    None
}

fn detect_ripgrep() -> bool {
    first_working(&[PathBuf::from("rg")], &["--version"]).is_some()
}

fn check_network() -> bool {
    match ("github.com", 443).to_socket_addrs() {
        Ok(mut addrs) => addrs
            .next()
            .map(|addr| {
                std::net::TcpStream::connect_timeout(&addr, Duration::from_secs(4)).is_ok()
            })
            .unwrap_or(false),
        Err(_) => false,
    }
}

pub fn check() -> PrereqStatus {
    let git_path = detect_git();
    let python_path = detect_python();
    PrereqStatus {
        git: git_path.is_some(),
        git_path,
        python: python_path.is_some(),
        python_path,
        ripgrep: detect_ripgrep(),
        network: check_network(),
    }
}

/// Attempt to auto-install missing prerequisites. Returns a human-readable status message.
pub fn install_prereqs() -> String {
    #[cfg(target_os = "windows")]
    {
        let mut msgs = Vec::new();
        if detect_git().is_none() {
            msgs.push(install_windows("Git.Git"));
        }
        if detect_python().is_none() {
            msgs.push(install_windows("Python.Python.3.12"));
        }
        if msgs.is_empty() {
            return "All required prerequisites are already installed.".into();
        }
        return msgs.join("\n");
    }
    #[cfg(target_os = "macos")]
    {
        if detect_git().is_none() {
            let _ = silent_command("xcode-select").arg("--install").spawn();
            return "Finish the Apple developer-tools dialog, then click Re-check.".into();
        }
        return "Install Python 3.12+ via Homebrew (brew install python) or python.org, then Re-check.".into();
    }
    #[cfg(all(not(target_os = "windows"), not(target_os = "macos")))]
    {
        "Install git and python3 via your package manager (e.g. apt install git python3), then Re-check.".into()
    }
}

#[cfg(target_os = "windows")]
fn install_windows(winget_id: &str) -> String {
    let result = silent_command("winget")
        .args([
            "install", "--id", winget_id, "--scope", "machine",
            "--silent", "--accept-package-agreements", "--accept-source-agreements",
        ])
        .output();
    match result {
        Ok(o) if o.status.success() => {
            format!("Installed {winget_id}. Restart the app so PATH updates take effect.")
        }
        _ => format!(
            "Could not install {winget_id} automatically via winget. Install it manually and Re-check."
        ),
    }
}
