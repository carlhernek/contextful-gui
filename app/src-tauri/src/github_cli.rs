//! GitHub CLI (`gh`) detection + auth status.
//!
//! When `gh` is installed, authenticated, and wired into git
//! (`gh auth setup-git`), HTTPS clones/pulls of private GitHub repos work
//! through git's credential helper — no Contextful PAT required. This module
//! lets the UI prefer and encourage that path.

use std::path::PathBuf;

use serde::Serialize;

use crate::prereqs::silent_command;

#[derive(Debug, Clone, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GithubCliStatus {
    pub installed: bool,
    pub authenticated: bool,
    pub login: Option<String>,
    /// git is configured to use `gh` as the github.com credential helper.
    pub git_configured: bool,
    pub gh_path: Option<String>,
}

fn runs_ok(path: &str, args: &[&str]) -> bool {
    silent_command(path)
        .args(args)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

fn detect_gh() -> Option<String> {
    let mut candidates = vec![PathBuf::from("gh")];
    #[cfg(target_os = "windows")]
    {
        if let Some(local) = std::env::var_os("LOCALAPPDATA") {
            candidates.push(PathBuf::from(local).join(r"Programs\GitHub CLI\gh.exe"));
        }
        candidates.push(PathBuf::from(r"C:\Program Files\GitHub CLI\gh.exe"));
    }
    #[cfg(not(target_os = "windows"))]
    {
        candidates.push(PathBuf::from("/opt/homebrew/bin/gh"));
        candidates.push(PathBuf::from("/usr/local/bin/gh"));
        candidates.push(PathBuf::from("/usr/bin/gh"));
        if let Some(home) = std::env::var_os("HOME") {
            candidates.push(PathBuf::from(home).join(".local/bin/gh"));
        }
    }
    for cand in &candidates {
        let p = cand.to_string_lossy().to_string();
        // Bare "gh" relies on PATH; absolute candidates must exist first.
        if (cand.is_absolute() && !cand.exists()) || !runs_ok(&p, &["--version"]) {
            continue;
        }
        return Some(p);
    }
    None
}

fn detect_git() -> String {
    let candidates = [
        "git",
        "/usr/bin/git",
        "/opt/homebrew/bin/git",
        "/usr/local/bin/git",
    ];
    for c in candidates {
        let is_abs = c.starts_with('/');
        if is_abs && !std::path::Path::new(c).exists() {
            continue;
        }
        if runs_ok(c, &["--version"]) {
            return c.to_string();
        }
    }
    "git".to_string()
}

/// Parse a login out of `gh auth status` text (".. account <login> ..").
fn parse_login(text: &str) -> Option<String> {
    let mut tokens = text.split_whitespace();
    while let Some(t) = tokens.next() {
        if t == "account" {
            if let Some(next) = tokens.next() {
                let login =
                    next.trim_matches(|c: char| !c.is_alphanumeric() && c != '-' && c != '_');
                if !login.is_empty() {
                    return Some(login.to_string());
                }
            }
        }
    }
    None
}

fn git_uses_gh_helper() -> bool {
    let git = detect_git();
    let out = silent_command(&git)
        .args(["config", "--get-all", "credential.https://github.com.helper"])
        .output();
    match out {
        Ok(o) => {
            let text = String::from_utf8_lossy(&o.stdout);
            text.contains("gh auth git-credential") || (o.status.success() && !text.trim().is_empty())
        }
        Err(_) => false,
    }
}

pub fn status() -> GithubCliStatus {
    let Some(gh_path) = detect_gh() else {
        return GithubCliStatus::default();
    };
    let (authenticated, parsed_login) = match silent_command(&gh_path)
        .args(["auth", "status"])
        .output()
    {
        Ok(o) => {
            let text = format!(
                "{}{}",
                String::from_utf8_lossy(&o.stdout),
                String::from_utf8_lossy(&o.stderr)
            );
            (o.status.success(), parse_login(&text))
        }
        Err(_) => (false, None),
    };
    // Best-effort username if status parsing came up empty (needs network).
    let login = parsed_login.or_else(|| {
        if !authenticated {
            return None;
        }
        silent_command(&gh_path)
            .args(["api", "user", "--jq", ".login"])
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .filter(|s| !s.is_empty())
    });
    GithubCliStatus {
        installed: true,
        authenticated,
        login,
        git_configured: authenticated && git_uses_gh_helper(),
        gh_path: Some(gh_path),
    }
}

/// Run `gh auth setup-git` so git uses the GitHub CLI credential helper.
pub fn setup_git() -> Result<String, String> {
    let gh = detect_gh().ok_or_else(|| "GitHub CLI (gh) not found".to_string())?;
    let out = silent_command(&gh)
        .args(["auth", "setup-git"])
        .output()
        .map_err(|e| e.to_string())?;
    if out.status.success() {
        Ok("git configured to use GitHub CLI for github.com".to_string())
    } else {
        let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
        Err(if stderr.is_empty() {
            "gh auth setup-git failed".to_string()
        } else {
            stderr
        })
    }
}

#[cfg(test)]
mod tests {
    use super::parse_login;

    #[test]
    fn parses_login_from_status() {
        let text = "github.com\n  ✓ Logged in to github.com account octocat (keyring)\n  - Active account: true";
        assert_eq!(parse_login(text).as_deref(), Some("octocat"));
    }

    #[test]
    fn parses_login_with_trailing_punctuation() {
        assert_eq!(parse_login("account devuser, foo").as_deref(), Some("devuser"));
    }

    #[test]
    fn none_when_absent() {
        assert_eq!(parse_login("not logged in"), None);
    }
}
