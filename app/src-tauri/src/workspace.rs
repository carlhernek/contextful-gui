//! Workspace, git worktrees, target-repo cloning, and module distribution (spec section 8).

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{bail, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine as _};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::git_credentials;
use crate::prereqs::silent_command;

/// TEMPLATE_REPO points the GUI repo at the files/template repo (spec 1.5).
/// Override with the CONTEXTFUL_TEMPLATE_REPO env var for offline dev / smoke tests.
const TEMPLATE_REPO: &str = "https://github.com/carlhernek/contextful-files.git";
const SYNC_PATHS: &[&str] = &["modules", "templates", "scripts", "agents"];

pub const META_FILE: &str = ".contextful.json";
pub const RUN_STATE_DIR: &str = "runs";
#[allow(dead_code)] // reserved for orchestrator chat persistence (spec 8.1)
pub const CHATLOG_FILE: &str = ".chatlog.json";
pub const TEMPLATE_VERSION_FILE: &str = "modules/template-version.txt";
const APP_VERSION: &str = env!("CARGO_PKG_VERSION");

fn template_repo_url() -> String {
    std::env::var("CONTEXTFUL_TEMPLATE_REPO").unwrap_or_else(|_| TEMPLATE_REPO.to_string())
}

// --- data models ----------------------------------------------------------
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RepoEntry {
    pub name: String,
    pub url: String,
    #[serde(default = "default_branch")]
    pub branch: String,
}

fn default_branch() -> String {
    "main".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectMeta {
    pub display_name: String,
    #[serde(default = "default_project_type")]
    pub project_type: String,
    #[serde(default)]
    pub repos: Vec<RepoEntry>,
    #[serde(default)]
    pub models: serde_json::Map<String, Value>,
    #[serde(default)]
    pub selected_modules: Vec<String>,
    #[serde(default)]
    pub deleted: bool,
}

fn default_project_type() -> String {
    "both".to_string()
}

impl ProjectMeta {
    fn new(display_name: &str) -> Self {
        Self {
            display_name: display_name.to_string(),
            project_type: default_project_type(),
            repos: Vec::new(),
            models: serde_json::Map::new(),
            selected_modules: Vec::new(),
            deleted: false,
        }
    }
}

// --- git helpers ----------------------------------------------------------
/// Extract hostname from https, http, or git@ URLs.
pub fn git_host_from_url(url: &str) -> Option<String> {
    let url = url.trim();
    if let Some(rest) = url.strip_prefix("https://").or_else(|| url.strip_prefix("http://")) {
        let authority = rest.split('/').next()?;
        return Some(authority.rsplit('@').next()?.to_string());
    }
    if let Some(rest) = url.strip_prefix("git@") {
        return rest.split(':').next().map(|s| s.to_string());
    }
    if let Some(rest) = url.strip_prefix("ssh://git@") {
        return rest.split('/').next().map(|s| s.to_string());
    }
    None
}

pub fn unique_git_hosts(urls: impl IntoIterator<Item = impl AsRef<str>>) -> Vec<String> {
    let mut hosts: Vec<String> = urls
        .into_iter()
        .filter_map(|u| git_host_from_url(u.as_ref()))
        .map(|h| git_credentials::normalize_host(&h))
        .collect();
    hosts.sort();
    hosts.dedup();
    hosts
}

fn https_user_and_path(url: &str) -> Option<(Option<String>, String)> {
    let (scheme_len, rest) = if let Some(r) = url.strip_prefix("https://") {
        (8, r)
    } else if let Some(r) = url.strip_prefix("http://") {
        (7, r)
    } else {
        return None;
    };
    let path_start = rest.find('/').unwrap_or(rest.len());
    let authority = &rest[..path_start];
    let path = &url[scheme_len + path_start..];
    if let Some((user, host)) = authority.rsplit_once('@') {
        let user = if user.is_empty() { None } else { Some(user.to_string()) };
        Some((user, format!("{host}{path}")))
    } else {
        Some((None, format!("{authority}{path}")))
    }
}

#[allow(dead_code)] // URL-embed fallback; exercised in unit tests
fn encode_userinfo(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char);
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

#[allow(dead_code)]
fn authenticated_https_url(url: &str, pat: &str) -> String {
    let Some((user, host_path)) = https_user_and_path(url) else {
        return url.to_string();
    };
    let enc_pat = encode_userinfo(pat);
    match user {
        Some(u) => format!("https://{}:{}@{host_path}", encode_userinfo(&u), enc_pat),
        None => format!("https://:{enc_pat}@{host_path}"),
    }
}

/// HTTPS remotes that typically require a stored PAT (Azure DevOps, embedded username URLs).
pub fn https_repo_needs_pat(url: &str) -> bool {
    let url = url.trim();
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return false;
    }
    if url.contains('@') {
        return true;
    }
    git_host_from_url(url).is_some_and(|h| {
        let h = h.to_lowercase();
        h == "dev.azure.com" || h.ends_with(".visualstudio.com")
    })
}

pub fn missing_pat_host(url: &str) -> Option<String> {
    if !https_repo_needs_pat(url) {
        return None;
    }
    let host = git_host_from_url(url)?;
    if git_credentials::load(&host).ok().flatten().is_some() {
        return None;
    }
    Some(git_credentials::normalize_host(&host))
}

fn inject_https_user(url: &str, user: &str) -> String {
    let Some((_, host_path)) = https_user_and_path(url) else {
        return url.to_string();
    };
    format!("https://{user}@{host_path}")
}

/// Prefer embedded URL user; else meta URL user; else plain remote URL.
pub fn resolve_git_auth_url(meta_url: &str, remote_url: &str) -> String {
    if https_user_and_path(remote_url)
        .map(|(u, _)| u.is_some())
        .unwrap_or(false)
    {
        return remote_url.to_string();
    }
    if let Some((Some(user), _)) = https_user_and_path(meta_url) {
        return inject_https_user(remote_url, &user);
    }
    remote_url.to_string()
}

fn git_https_username(host: &str, url: &str) -> Option<String> {
    if let Some((Some(user), _)) = https_user_and_path(url) {
        return Some(user);
    }
    git_credentials::load_user(host).ok().flatten()
}

fn append_git_auth(cmd: &mut Command, url: &str) {
    let Some(host) = git_host_from_url(url) else {
        return;
    };
    let Ok(Some(pat)) = git_credentials::load(&host) else {
        return;
    };
    let creds = match git_https_username(&host, url) {
        Some(u) => format!("{u}:{pat}"),
        None => format!(":{pat}"),
    };
    let header = STANDARD.encode(creds);
    cmd.arg("-c")
        .arg(format!("http.extraHeader=Authorization: Basic {header}"));
}

fn git_command() -> Command {
    let mut cmd = silent_command("git");
    cmd.env("GIT_TERMINAL_PROMPT", "0")
        .env("GCM_INTERACTIVE", "Never")
        .env("GIT_PAGER", "cat");
    cmd
}

fn git_run(args: &[&str], cwd: &Path) -> Result<String> {
    git_run_authed(args, cwd, None)
}

fn git_run_authed(args: &[&str], cwd: &Path, auth_url: Option<&str>) -> Result<String> {
    let mut cmd = git_command();
    if let Some(url) = auth_url {
        append_git_auth(&mut cmd, url);
    }
    let output = cmd
        .args(args)
        .current_dir(cwd)
        .output()
        .with_context(|| format!("run git {args:?}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        bail!("git {args:?} failed: {stderr}");
    }
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

fn git_run_anywhere(args: &[&str]) -> Result<String> {
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    git_run(args, &cwd)
}

/// Git commands permitted on read-only target repos (no push/commit).
const REPO_GIT_ALLOWED: &[&str] = &[
    "clone", "fetch", "pull", "merge", "status", "rev-parse", "branch", "log", "config", "remote",
];

fn repo_git_run(args: &[&str], cwd: &Path) -> Result<String> {
    repo_git_run_authed(args, cwd, None)
}

fn repo_git_run_authed(args: &[&str], cwd: &Path, auth_url: Option<&str>) -> Result<String> {
    let Some(cmd) = args.first() else {
        bail!("empty git args");
    };
    if !REPO_GIT_ALLOWED.contains(cmd) {
        bail!("git command '{cmd}' is not allowed on target repositories");
    }
    if args.iter().any(|a| *a == "push") {
        bail!("git push is disabled on target repositories");
    }
    git_run_authed(args, cwd, auth_url)
}

fn harden_readonly_repo(repo_dir: &Path) -> Result<()> {
    if !repo_dir.join(".git").exists() {
        return Ok(());
    }
    let _ = repo_git_run(&["config", "push.default", "nothing"], repo_dir);
    let _ = repo_git_run(
        &["config", "remote.origin.pushurl", "DISABLED://contextful-no-push"],
        repo_dir,
    );
    let hook_dir = repo_dir.join(".git").join("hooks");
    fs::create_dir_all(&hook_dir).ok();
    let hook = hook_dir.join("pre-push");
    if !hook.exists() {
        fs::write(
            &hook,
            "#!/bin/sh\necho 'Contextful: push disabled' >&2\nexit 1\n",
        )
        .context("write pre-push hook")?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = fs::metadata(&hook)?.permissions();
            perms.set_mode(0o755);
            fs::set_permissions(&hook, perms)?;
        }
    }
    Ok(())
}

fn maybe_harden_cloned_repos(project: &Path, meta: &ProjectMeta) {
    for repo in &meta.repos {
        let dest = project.join("repos").join(&repo.name);
        if dest.join(".git").exists() {
            let _ = harden_readonly_repo(&dest);
        }
    }
}

/// Non-destructive touch when opening a project (lazy migration hooks).
pub fn touch_project(project: &Path) {
    let _ = fs::create_dir_all(project.join("meta"));
    if let Ok(meta) = read_meta(project) {
        maybe_harden_cloned_repos(project, &meta);
    }
}

// --- template clone + worktree projects -----------------------------------
pub fn clone_template(install: &Path) -> Result<PathBuf> {
    let template = install.join("template");
    if template.join(".git").exists() {
        return Ok(template); // idempotent
    }
    fs::create_dir_all(install).context("create install dir")?;
    let url = template_repo_url();
    git_run_anywhere(&["clone", &url, template.to_str().context("template path")?])
        .context("clone template repo")?;
    Ok(template)
}

pub fn setup_template(install: &Path) -> Result<Value> {
    let template = clone_template(install)?;
    let version = read_template_version(&template);
    Ok(json!({"ok": true, "template": template.to_string_lossy(), "version": version}))
}

fn read_template_version(root: &Path) -> String {
    fs::read_to_string(root.join(TEMPLATE_VERSION_FILE))
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|_| "unknown".to_string())
}

pub fn create_project(install: &Path, id: &str, display_name: &str) -> Result<PathBuf> {
    let template = clone_template(install)?;
    let branch = format!(
        "project/{id}-{}",
        chrono::Local::now().format("%Y%m%d-%H%M%S")
    );
    let project = install.join("projects").join(id);
    if project.exists() {
        bail!("project '{id}' already exists");
    }
    fs::create_dir_all(install.join("projects")).context("create projects dir")?;
    git_run(
        &[
            "worktree",
            "add",
            project.to_str().context("project path")?,
            "-b",
            &branch,
        ],
        &template,
    )
    .context("git worktree add")?;
    init_workspace_dirs(&project)?;
    write_meta(&project, &ProjectMeta::new(display_name))?;
    Ok(project)
}

fn init_workspace_dirs(project: &Path) -> Result<()> {
    for sub in ["repos", "meta", "research", RUN_STATE_DIR] {
        fs::create_dir_all(project.join(sub)).with_context(|| format!("mkdir {sub}"))?;
    }
    let eventlog = project.join(".eventlog");
    if !eventlog.exists() {
        fs::write(&eventlog, "").context("init eventlog")?;
    }
    Ok(())
}

// --- meta (.contextful.json) ----------------------------------------------
pub fn read_meta(project: &Path) -> Result<ProjectMeta> {
    let raw = fs::read_to_string(project.join(META_FILE)).context("read .contextful.json")?;
    serde_json::from_str(&raw).context("parse .contextful.json")
}

pub fn write_meta(project: &Path, meta: &ProjectMeta) -> Result<()> {
    let raw = serde_json::to_string_pretty(meta)?;
    fs::write(project.join(META_FILE), raw).context("write .contextful.json")
}

pub fn project_dir(install: &Path, id: &str) -> PathBuf {
    install.join("projects").join(id)
}

// --- projects -------------------------------------------------------------
pub fn suggest_project_id(install: &Path, display_name: &str) -> String {
    let base: String = display_name
        .to_lowercase()
        .chars()
        .map(|c| if c.is_alphanumeric() { c } else { '-' })
        .collect();
    let base = base.trim_matches('-').to_string();
    let base = if base.is_empty() { "project".to_string() } else { base };
    let projects = install.join("projects");
    let mut candidate = base.clone();
    let mut n = 2;
    while projects.join(&candidate).exists() {
        candidate = format!("{base}-{n}");
        n += 1;
    }
    candidate
}

pub fn list_projects(install: &Path) -> Vec<Value> {
    let projects = install.join("projects");
    let mut out = Vec::new();
    let Ok(entries) = fs::read_dir(&projects) else {
        return out;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        let id = entry.file_name().to_string_lossy().to_string();
        if let Ok(meta) = read_meta(&path) {
            if meta.deleted {
                continue;
            }
            touch_project(&path);
            out.push(json!({
                "id": id,
                "display_name": meta.display_name,
                "project_type": meta.project_type,
                "repos": meta.repos,
            }));
        }
    }
    out.sort_by(|a, b| {
        a["display_name"].as_str().unwrap_or("").cmp(b["display_name"].as_str().unwrap_or(""))
    });
    out
}

pub fn rename_project(install: &Path, id: &str, display_name: &str) -> Result<()> {
    let project = project_dir(install, id);
    let mut meta = read_meta(&project)?;
    meta.display_name = display_name.to_string();
    write_meta(&project, &meta)
}

pub fn delete_project(install: &Path, id: &str) -> Result<()> {
    let project = project_dir(install, id);
    let mut meta = read_meta(&project)?;
    meta.deleted = true; // soft-delete: folder retained, hidden from list
    write_meta(&project, &meta)
}

pub fn set_project_type(install: &Path, id: &str, project_type: &str) -> Result<()> {
    let project = project_dir(install, id);
    let mut meta = read_meta(&project)?;
    meta.project_type = project_type.to_string();
    write_meta(&project, &meta)
}

pub fn set_models(install: &Path, id: &str, models: serde_json::Map<String, Value>) -> Result<()> {
    let project = project_dir(install, id);
    let mut meta = read_meta(&project)?;
    meta.models = models;
    write_meta(&project, &meta)
}

// --- target repos ---------------------------------------------------------
pub fn add_repo(install: &Path, id: &str, name: &str, url: &str, branch: &str) -> Result<()> {
    let project = project_dir(install, id);
    let mut meta = read_meta(&project)?;
    if meta.repos.iter().any(|r| r.name == name) {
        bail!("a repo named '{name}' already exists");
    }
    meta.repos.push(RepoEntry {
        name: name.to_string(),
        url: url.to_string(),
        branch: if branch.is_empty() { default_branch() } else { branch.to_string() },
    });
    write_meta(&project, &meta)
}

pub fn remove_repo(install: &Path, id: &str, name: &str) -> Result<()> {
    let project = project_dir(install, id);
    let mut meta = read_meta(&project)?;
    meta.repos.retain(|r| r.name != name);
    write_meta(&project, &meta)?;
    let dir = project.join("repos").join(name);
    if dir.exists() {
        let _ = fs::remove_dir_all(dir);
    }
    Ok(())
}

pub fn clone_repos(install: &Path, id: &str) -> Result<Value> {
    let project = project_dir(install, id);
    let meta = read_meta(&project)?;
    let repos_dir = project.join("repos");
    fs::create_dir_all(&repos_dir).ok();
    append_eventlog(
        &project,
        "git",
        "START",
        &format!("clone all ({} repos)", meta.repos.len()),
    );
    let mut results = Vec::new();
    for repo in &meta.repos {
        let dest = repos_dir.join(&repo.name);
        if dest.join(".git").exists() {
            let _ = harden_readonly_repo(&dest);
            append_eventlog(
                &project,
                "git",
                "SUCCESS",
                &format!("{} already cloned", repo.name),
            );
            results.push(json!({"name": repo.name, "ok": true, "status": "already cloned"}));
            continue;
        }
        if let Some(host) = missing_pat_host(&repo.url) {
            let msg = format!(
                "No Personal Access Token saved for {host} — add one under Git credentials"
            );
            append_eventlog(
                &project,
                "git",
                "WARN",
                &format!("{} skipped — {msg}", repo.name),
            );
            results.push(json!({
                "name": repo.name,
                "ok": false,
                "error": msg,
                "kind": "auth",
            }));
            continue;
        }
        match clone_one(&repo.url, &repo.branch, &dest) {
            Ok(()) => {
                let _ = harden_readonly_repo(&dest);
                append_eventlog(
                    &project,
                    "git",
                    "SUCCESS",
                    &format!("{} cloned (branch {})", repo.name, repo.branch),
                );
                results.push(json!({"name": repo.name, "ok": true, "status": "cloned"}));
            }
            Err(e) => {
                let msg = e.to_string();
                let kind = classify_git_error(&msg);
                append_eventlog(
                    &project,
                    "git",
                    "ERROR",
                    &format!("{} clone failed — {msg}", repo.name),
                );
                results.push(json!({"name": repo.name, "ok": false, "error": msg, "kind": kind}));
            }
        }
    }
    let ok_count = results.iter().filter(|r| r["ok"].as_bool() == Some(true)).count();
    append_git_batch_summary(&project, "clone", ok_count, meta.repos.len());
    Ok(json!({"results": results}))
}

fn append_git_batch_summary(project: &Path, operation: &str, ok_count: usize, total: usize) {
    let status = if ok_count == total { "SUCCESS" } else { "ERROR" };
    append_eventlog(
        project,
        "git",
        status,
        &format!("{operation} finished ({ok_count}/{total} ok)"),
    );
}

fn repo_remote_url(repo_dir: &Path) -> Option<String> {
    repo_git_run(&["remote", "get-url", "origin"], repo_dir)
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

fn clone_one(url: &str, branch: &str, dest: &Path) -> Result<()> {
    let cwd = dest.parent().unwrap_or(Path::new("."));
    let dest_str = dest.to_str().context("dest path")?;
    repo_git_run_authed(
        &[
            "clone",
            "--depth",
            "1",
            "--branch",
            branch,
            url,
            dest_str,
        ],
        cwd,
        Some(url),
    )
    .map(|_| ())
    .or_else(|_| {
        repo_git_run_authed(
            &["clone", "--depth", "1", url, dest_str],
            cwd,
            Some(url),
        )
        .map(|_| ())
    })
}

fn classify_git_error(stderr: &str) -> &'static str {
    let s = stderr.to_lowercase();
    if s.contains("authentication")
        || s.contains("permission denied")
        || s.contains("could not read")
        || s.contains("terminal prompts disabled")
        || s.contains("403")
        || s.contains("401")
        || s.contains("publickey")
    {
        "auth"
    } else if s.contains("could not resolve")
        || s.contains("timed out")
        || s.contains("network")
        || s.contains("connection")
    {
        "transient"
    } else {
        "other"
    }
}

/// True when any entry in a clone/pull results payload failed.
pub fn git_batch_has_failures(results: &Value) -> bool {
    results
        .get("results")
        .and_then(|r| r.as_array())
        .is_some_and(|arr| arr.iter().any(|r| r.get("ok") == Some(&json!(false))))
}

pub fn git_batch_failure_message(results: &Value) -> String {
    let Some(arr) = results.get("results").and_then(|r| r.as_array()) else {
        return "repository operation failed".to_string();
    };
    let failed = arr.iter().filter(|r| r.get("ok") == Some(&json!(false))).count();
    let auth = arr
        .iter()
        .filter(|r| r.get("ok") == Some(&json!(false)) && r.get("kind") == Some(&json!("auth")))
        .count();
    if auth > 0 {
        format!(
            "{failed}/{} failed — check Git credentials for dev.azure.com (PAT/password and Azure username, e.g. from Generate Git Credentials)",
            arr.len()
        )
    } else {
        format!("{failed}/{} repositories failed", arr.len())
    }
}

pub fn list_repos(install: &Path, id: &str) -> Result<Vec<Value>> {
    let project = project_dir(install, id);
    touch_project(&project);
    let meta = read_meta(&project)?;
    maybe_harden_cloned_repos(&project, &meta);
    Ok(meta
        .repos
        .into_iter()
        .map(|r| {
            let repo_path = project.join("repos").join(&r.name);
            let cloned = repo_path.join(".git").exists();
            let head = if cloned {
                repo_git_run(&["rev-parse", "--short", "HEAD"], &repo_path)
                    .ok()
                    .map(|s| s.trim().to_string())
            } else {
                None
            };
            json!({"name": r.name, "url": r.url, "branch": r.branch, "cloned": cloned, "head": head})
        })
        .collect())
}

pub fn pull_repos(install: &Path, id: &str) -> Result<Value> {
    let project = project_dir(install, id);
    touch_project(&project);
    let meta = read_meta(&project)?;
    append_eventlog(
        &project,
        "git",
        "START",
        &format!("pull all ({} repos)", meta.repos.len()),
    );
    let mut results = Vec::new();
    for repo in &meta.repos {
        let dest = project.join("repos").join(&repo.name);
        if !dest.join(".git").exists() {
            append_eventlog(
                &project,
                "git",
                "ERROR",
                &format!("{} not cloned — skipped", repo.name),
            );
            results.push(json!({
                "name": repo.name,
                "ok": false,
                "error": "not cloned",
                "kind": "other",
            }));
            continue;
        }
        let _ = harden_readonly_repo(&dest);
        let branch = if repo.branch.is_empty() {
            default_branch()
        } else {
            repo.branch.clone()
        };
        let remote_url = repo_remote_url(&dest).unwrap_or_else(|| repo.url.clone());
        let auth_url = resolve_git_auth_url(&repo.url, &remote_url);
        let fetch_result = repo_git_run_authed(&["fetch", "origin", &branch], &dest, Some(&auth_url));
        match fetch_result {
            Ok(_) => {
                let merge_ref = format!("origin/{branch}");
                match repo_git_run(&["merge", "--ff-only", &merge_ref], &dest) {
                    Ok(_) => {
                        let head = repo_git_run(&["rev-parse", "--short", "HEAD"], &dest)
                            .ok()
                            .map(|s| s.trim().to_string());
                        append_eventlog(
                            &project,
                            "git",
                            "SUCCESS",
                            &format!(
                                "{} pulled origin/{branch}{}",
                                repo.name,
                                head.as_ref().map(|h| format!(" @{h}")).unwrap_or_default()
                            ),
                        );
                        results.push(json!({
                            "name": repo.name,
                            "ok": true,
                            "status": "updated",
                            "head": head,
                        }));
                    }
                    Err(e) => {
                        let msg = e.to_string();
                        append_eventlog(
                            &project,
                            "git",
                            "ERROR",
                            &format!("{} pull failed — {msg}", repo.name),
                        );
                        results.push(json!({
                            "name": repo.name,
                            "ok": false,
                            "error": msg,
                            "kind": classify_git_error(&msg),
                        }));
                    }
                }
            }
            Err(e) => {
                let msg = e.to_string();
                append_eventlog(
                    &project,
                    "git",
                    "ERROR",
                    &format!("{} fetch failed — {msg}", repo.name),
                );
                results.push(json!({
                    "name": repo.name,
                    "ok": false,
                    "error": msg,
                    "kind": classify_git_error(&msg),
                }));
            }
        }
    }
    let ok_count = results.iter().filter(|r| r["ok"].as_bool() == Some(true)).count();
    append_git_batch_summary(&project, "pull", ok_count, meta.repos.len());
    Ok(json!({"results": results}))
}

// --- meta docs ------------------------------------------------------------
#[derive(Debug, Clone, Serialize)]
pub struct MetaEntry {
    pub name: String,
    pub path: String,
    pub kind: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub size: Option<u64>,
}

fn meta_rel_to_path(project: &Path, rel_path: &str) -> Result<PathBuf> {
    let meta_root = project.join("meta");
    fs::create_dir_all(&meta_root).ok();
    let rel = rel_path.trim().trim_matches('/').replace('\\', "/");
    if rel.contains("..") {
        bail!("path traversal not allowed");
    }
    Ok(if rel.is_empty() {
        meta_root
    } else {
        meta_root.join(rel)
    })
}

fn resolve_meta_path(project: &Path, rel_path: &str) -> Result<PathBuf> {
    let target = meta_rel_to_path(project, rel_path)?;
    if !target.exists() {
        bail!("not found: meta/{rel_path}");
    }
    let meta_root = meta_rel_to_path(project, "")?;
    let root_canon = meta_root.canonicalize().unwrap_or(meta_root);
    let canonical = target
        .canonicalize()
        .with_context(|| format!("resolve meta/{rel_path}"))?;
    if !canonical.starts_with(&root_canon) {
        bail!("path escapes meta directory");
    }
    Ok(canonical)
}

const META_UPLOAD_EXTENSIONS: &[&str] = &[
    "txt", "md", "markdown", "docx", "doc", "pdf", "rtf",
    "csv", "xlsx", "xls", "xlsm",
    "jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico",
    "json", "yaml", "yml", "xml", "toml", "ini", "cfg", "log",
    "html", "htm", "css", "py", "js", "ts", "tsx", "jsx", "rs", "go", "java", "sh", "sql",
];

fn meta_upload_allowed(name: &str) -> bool {
    Path::new(name)
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| {
            let lower = e.to_lowercase();
            META_UPLOAD_EXTENSIONS.contains(&lower.as_str())
        })
        .unwrap_or(false)
}

pub fn list_meta_dir(install: &Path, id: &str, rel_path: &str) -> Result<Vec<MetaEntry>> {
    let project = project_dir(install, id);
    touch_project(&project);
    let dir = resolve_meta_path(&project, rel_path)?;
    if !dir.is_dir() {
        bail!("not a directory");
    }
    let mut out = Vec::new();
    for entry in fs::read_dir(&dir).with_context(|| format!("read meta dir {}", dir.display()))? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().to_string();
        let path = if rel_path.trim().trim_matches('/').is_empty() {
            name.clone()
        } else {
            format!("{}/{}", rel_path.trim().trim_matches('/'), name)
        };
        let meta = entry.metadata()?;
        if meta.is_dir() {
            out.push(MetaEntry {
                name,
                path,
                kind: "dir".to_string(),
                size: None,
            });
        } else if meta.is_file() {
            out.push(MetaEntry {
                name,
                path,
                kind: "file".to_string(),
                size: Some(meta.len()),
            });
        }
    }
    out.sort_by(|a, b| {
        let a_dir = a.kind == "dir";
        let b_dir = b.kind == "dir";
        match (a_dir, b_dir) {
            (true, false) => std::cmp::Ordering::Less,
            (false, true) => std::cmp::Ordering::Greater,
            _ => a.name.to_lowercase().cmp(&b.name.to_lowercase()),
        }
    });
    Ok(out)
}

pub fn upload_meta_files(
    install: &Path,
    id: &str,
    sources: &[String],
    dest_rel_path: &str,
) -> Result<Vec<String>> {
    let project = project_dir(install, id);
    let dest_dir = meta_rel_to_path(&project, dest_rel_path)?;
    if !dest_dir.is_dir() {
        bail!("upload destination is not a directory");
    }
    let mut copied = Vec::new();
    for src in sources {
        let src_path = Path::new(src);
        if src_path.is_dir() {
            bail!("folder upload is not supported — upload files or create folders in the app");
        }
        let Some(name) = src_path.file_name().and_then(|n| n.to_str()) else {
            continue;
        };
        if !meta_upload_allowed(name) {
            bail!("unsupported file type: {name}");
        }
        let dest = dest_dir.join(name);
        fs::copy(src_path, &dest).with_context(|| format!("copy {src}"))?;
        copied.push(if dest_rel_path.is_empty() {
            name.to_string()
        } else {
            format!("{dest_rel_path}/{name}")
        });
    }
    Ok(copied)
}

pub fn create_meta_dir(install: &Path, id: &str, rel_path: &str) -> Result<()> {
    let project = project_dir(install, id);
    let target = meta_rel_to_path(&project, rel_path)?;
    if target.exists() {
        bail!("already exists: {rel_path}");
    }
    fs::create_dir(&target).with_context(|| format!("create meta/{rel_path}"))?;
    Ok(())
}

pub fn rename_meta_entry(install: &Path, id: &str, rel_path: &str, new_name: &str) -> Result<String> {
    let new_name = new_name.trim().trim_matches('/').replace('\\', "/");
    if new_name.is_empty() || new_name.contains('/') || new_name.contains("..") {
        bail!("invalid name");
    }
    let project = project_dir(install, id);
    let from = resolve_meta_path(&project, rel_path)?;
    let parent = from
        .parent()
        .context("meta entry has no parent")?;
    let to = parent.join(&new_name);
    if to.exists() {
        bail!("'{new_name}' already exists");
    }
    fs::rename(&from, &to).with_context(|| format!("rename {rel_path}"))?;
    let rel = rel_path.trim().trim_matches('/').replace('\\', "/");
    let new_rel = if rel.contains('/') {
        format!("{}/{}", rel.rsplit_once('/').map(|(p, _)| p).unwrap_or(""), new_name)
    } else {
        new_name.clone()
    };
    Ok(new_rel)
}

pub fn move_meta_entry(install: &Path, id: &str, from_rel: &str, dest_dir_rel: &str) -> Result<String> {
    let project = project_dir(install, id);
    let from = resolve_meta_path(&project, from_rel)?;
    let dest_dir = meta_rel_to_path(&project, dest_dir_rel)?;
    if !dest_dir.is_dir() {
        bail!("destination is not a directory");
    }
    let name = from
        .file_name()
        .and_then(|n| n.to_str())
        .context("entry has no name")?;
    let to = dest_dir.join(name);
    if to.exists() {
        bail!("'{name}' already exists in destination");
    }
    fs::rename(&from, &to).with_context(|| format!("move {from_rel}"))?;
    Ok(if dest_dir_rel.is_empty() {
        name.to_string()
    } else {
        format!("{dest_dir_rel}/{name}")
    })
}

pub fn list_meta_files(install: &Path, id: &str) -> Vec<Value> {
    let meta_dir = project_dir(install, id).join("meta");
    let mut out = Vec::new();
    if let Ok(entries) = fs::read_dir(&meta_dir) {
        for e in entries.flatten() {
            if e.path().is_file() {
                let size = e.metadata().map(|m| m.len()).unwrap_or(0);
                out.push(json!({"name": e.file_name().to_string_lossy(), "size": size}));
            }
        }
    }
    out
}

pub fn delete_meta_entry(install: &Path, id: &str, rel_path: &str) -> Result<()> {
    let project = project_dir(install, id);
    let path = resolve_meta_path(&project, rel_path)?;
    if path.is_file() {
        fs::remove_file(&path).context("delete meta file")?;
    } else if path.is_dir() {
        fs::remove_dir_all(&path).context("delete meta dir")?;
    }
    Ok(())
}

pub fn delete_meta_file(install: &Path, id: &str, name: &str) -> Result<()> {
    delete_meta_entry(install, id, name)
}

// --- chatlog --------------------------------------------------------------
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
    pub ts: String,
}

pub fn load_chatlog(project: &Path) -> Vec<ChatMessage> {
    let path = project.join(CHATLOG_FILE);
    if !path.exists() {
        return Vec::new();
    }
    let raw = match fs::read_to_string(&path) {
        Ok(s) => s,
        Err(_) => return Vec::new(),
    };
    match serde_json::from_str::<Vec<ChatMessage>>(&raw) {
        Ok(msgs) => msgs,
        Err(_) => {
            let backup = project.join(format!("{CHATLOG_FILE}.bak"));
            let _ = fs::copy(&path, &backup);
            append_eventlog(project, "gui", "ERROR", "reset malformed chatlog");
            let _ = fs::write(&path, "[]");
            Vec::new()
        }
    }
}

pub fn append_chatlog(project: &Path, role: &str, content: &str) -> Result<()> {
    let mut msgs = load_chatlog(project);
    msgs.push(ChatMessage {
        role: role.to_string(),
        content: content.to_string(),
        ts: chrono::Local::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true),
    });
    let raw = serde_json::to_string_pretty(&msgs)?;
    fs::write(project.join(CHATLOG_FILE), raw).context("write chatlog")?;
    Ok(())
}

// --- modules --------------------------------------------------------------
pub fn list_modules(install: &Path, id: &str) -> Vec<Value> {
    let modules_dir = project_dir(install, id).join("modules");
    let mut out = Vec::new();
    if let Ok(entries) = fs::read_dir(&modules_dir) {
        for e in entries.flatten() {
            let path = e.path();
            if path.is_dir() && path.join("SKILL.md").exists() {
                let mid = e.file_name().to_string_lossy().to_string();
                let title = module_title(&path.join("SKILL.md")).unwrap_or_else(|| readable(&mid));
                let description = module_description(&path.join("SKILL.md"));
                out.push(json!({
                    "id": mid,
                    "title": title,
                    "description": description,
                    "packs": packs_for(&mid),
                }));
            }
        }
    }
    out.sort_by(|a, b| {
        let a_id = a["id"].as_str().unwrap_or("");
        let b_id = b["id"].as_str().unwrap_or("");
        if a_id == "workspace-index" {
            return std::cmp::Ordering::Less;
        }
        if b_id == "workspace-index" {
            return std::cmp::Ordering::Greater;
        }
        a_id.cmp(b_id)
    });
    out
}

fn module_title(skill: &Path) -> Option<String> {
    let content = fs::read_to_string(skill).ok()?;
    content
        .lines()
        .find(|l| l.starts_with("# "))
        .map(|l| l.trim_start_matches("# ").trim().to_string())
}

/// First paragraph line under `## Scope` in SKILL.md (max 160 chars).
fn module_description(skill: &Path) -> Option<String> {
    let content = fs::read_to_string(skill).ok()?;
    let mut in_scope = false;
    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed == "## Scope" {
            in_scope = true;
            continue;
        }
        if in_scope {
            if trimmed.starts_with("## ") {
                break;
            }
            if !trimmed.is_empty() {
                let desc: String = trimmed.chars().take(160).collect();
                return Some(desc);
            }
        }
    }
    None
}

fn readable(id: &str) -> String {
    id.split('-')
        .map(|w| {
            let mut chars = w.chars();
            match chars.next() {
                Some(c) => c.to_uppercase().collect::<String>() + chars.as_str(),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn packs_for(module_id: &str) -> Vec<&'static str> {
    if module_id == "workspace-index" {
        return vec!["Core"];
    }
    let mut packs = Vec::new();
    let engineering = [
        "security-analysis",
        "accessibility-pass",
        "dependency-health",
        "cicd-pipeline-audit",
        "api-surface-analysis",
        "tech-debt-triage",
        "test-coverage",
    ];
    let sales = [
        "swot-analysis",
        "b2b-low-hanging-features",
        "b2c-campaign-ideas",
        "pricing-packaging-friction",
        "localization-readiness",
    ];
    let docs = ["accessibility-pass", "onboarding-flow-audit", "documentation-gap-analysis"];
    let compliance = ["security-analysis", "data-privacy-scan", "licensing-compatibility"];
    if engineering.contains(&module_id) {
        packs.push("Engineering");
    }
    if sales.contains(&module_id) {
        packs.push("Sales & Growth");
    }
    if docs.contains(&module_id) {
        packs.push("Onboarding & Docs");
    }
    if compliance.contains(&module_id) {
        packs.push("Compliance & Risk");
    }
    packs
}

/// Suggest a default module set from repo signals + project type (spec 9.3).
pub fn get_module_suggestions(install: &Path, id: &str) -> Vec<String> {
    let project = project_dir(install, id);
    let meta = read_meta(&project).unwrap_or_else(|_| ProjectMeta::new(id));
    let repos = project.join("repos");
    let mut suggested = vec![
        "security-analysis".to_string(),
        "tech-debt-triage".to_string(),
        "documentation-gap-analysis".to_string(),
    ];

    let has = |rel: &str| repos.read_dir().ok().into_iter().flatten().flatten().any(|e| {
        let p = e.path();
        p.join(rel).exists()
    });

    if has(".github/workflows") {
        suggested.push("cicd-pipeline-audit".to_string());
    }
    if has("package.json") {
        suggested.push("dependency-health".to_string());
        suggested.push("accessibility-pass".to_string());
        suggested.push("api-surface-analysis".to_string());
    }
    match meta.project_type.as_str() {
        "b2b" => suggested.push("b2b-low-hanging-features".to_string()),
        "b2c" => suggested.push("b2c-campaign-ideas".to_string()),
        _ => {
            suggested.push("b2b-low-hanging-features".to_string());
            suggested.push("b2c-campaign-ideas".to_string());
        }
    }
    suggested.sort();
    suggested.dedup();
    suggested
}

pub fn get_module_selection(install: &Path, id: &str) -> Vec<String> {
    let project = project_dir(install, id);
    read_meta(&project)
        .map(|m| m.selected_modules)
        .unwrap_or_default()
}

pub fn set_module_selection(install: &Path, id: &str, modules: Vec<String>) -> Result<Vec<String>> {
    let project = project_dir(install, id);
    let valid: std::collections::HashSet<String> = list_modules(install, id)
        .into_iter()
        .filter_map(|v| v["id"].as_str().map(String::from))
        .collect();
    let filtered: Vec<String> = modules
        .into_iter()
        .filter(|m| valid.contains(m))
        .collect();
    let mut meta = read_meta(&project)?;
    meta.selected_modules = filtered.clone();
    write_meta(&project, &meta)?;
    Ok(filtered)
}

// --- logs, runs, artifacts ------------------------------------------------
pub fn get_event_log(install: &Path, id: &str) -> String {
    fs::read_to_string(project_dir(install, id).join(".eventlog")).unwrap_or_default()
}

pub fn get_run_log(install: &Path, id: &str, run_id: &str) -> String {
    // Per-run view is filtered from the project event log by scope prefixes.
    let full = get_event_log(install, id);
    full.lines()
        .filter(|l| l.contains(&format!("runId={run_id}")) || l.contains(run_id))
        .collect::<Vec<_>>()
        .join("\n")
}

/// Belt-and-suspenders: persist terminal run status when the sidecar RPC fails or returns cancelled.
pub fn set_run_status(install: &Path, id: &str, run_id: &str, status: &str) -> Result<()> {
    let project = project_dir(install, id);
    let run_dir = project.join(RUN_STATE_DIR).join(run_id);
    if !run_dir.is_dir() {
        bail!("run directory missing");
    }
    let state_path = run_dir.join(".run-state.json");
    let mut state = fs::read_to_string(&state_path)
        .ok()
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
        .unwrap_or_else(|| json!({"runId": run_id, "status": "idle", "completedModules": []}));
    let prev = state
        .get("status")
        .and_then(|s| s.as_str())
        .unwrap_or("idle")
        .to_string();
    if prev == status {
        return Ok(());
    }
    if let Some(obj) = state.as_object_mut() {
        obj.insert("status".into(), json!(status));
        obj.insert(
            "updatedAt".into(),
            json!(chrono::Local::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true)),
        );
    }
    fs::write(&state_path, serde_json::to_string_pretty(&state)?)?;
    append_eventlog(
        &project,
        "run",
        "STATE",
        &format!("runId={run_id} {prev} -> {status} (gui fallback)"),
    );
    Ok(())
}

pub fn list_runs(install: &Path, id: &str) -> Vec<Value> {
    let runs_dir = project_dir(install, id).join(RUN_STATE_DIR);
    let mut out = Vec::new();
    if let Ok(entries) = fs::read_dir(&runs_dir) {
        for e in entries.flatten() {
            if !e.path().is_dir() {
                continue;
            }
            let run_id = e.file_name().to_string_lossy().to_string();
            let state = fs::read_to_string(e.path().join(".run-state.json"))
                .ok()
                .and_then(|s| serde_json::from_str::<Value>(&s).ok())
                .unwrap_or_else(|| json!({"runId": run_id, "status": "idle"}));
            out.push(state);
        }
    }
    out.sort_by(|a, b| b["runId"].as_str().unwrap_or("").cmp(a["runId"].as_str().unwrap_or("")));
    out
}

pub fn get_run_state(install: &Path, id: &str, run_id: &str) -> Value {
    let state_path = project_dir(install, id)
        .join(RUN_STATE_DIR)
        .join(run_id)
        .join(".run-state.json");
    fs::read_to_string(&state_path)
        .ok()
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
        .unwrap_or_else(|| {
            json!({
                "runId": run_id,
                "status": "idle",
                "completedModules": [],
                "failedModule": null,
                "error": null,
                "updatedAt": null,
            })
        })
}

pub fn get_run_artifacts(install: &Path, id: &str, run_id: &str) -> Value {
    let run_dir = project_dir(install, id).join(RUN_STATE_DIR).join(run_id);
    let mut modules = Vec::new();
    if let Ok(entries) = fs::read_dir(&run_dir) {
        for e in entries.flatten() {
            if !e.path().is_dir() {
                continue;
            }
            let mid = e.file_name().to_string_lossy().to_string();
            let analysis_path = e.path().join("analysis.md");
            let has_analysis = analysis_path.exists();
            let analysis = if has_analysis {
                fs::read_to_string(&analysis_path).ok()
            } else {
                None
            };
            let tasks_path = e.path().join("tasks.json");
            let tasks = fs::read_to_string(&tasks_path)
                .ok()
                .and_then(|s| serde_json::from_str::<Value>(&s).ok());
            let has_activity = e.path().join("activity.jsonl").exists();
            modules.push(json!({
                "moduleId": mid,
                "hasAnalysis": has_analysis,
                "hasActivity": has_activity,
                "analysis": analysis,
                "tasks": tasks,
            }));
        }
    }
    let summary = fs::read_to_string(run_dir.join("run-summary.md")).ok();
    json!({"runId": run_id, "modules": modules, "summary": summary})
}

pub fn get_run_activity(install: &Path, id: &str, run_id: &str, module_id: &str) -> Value {
    let path = project_dir(install, id)
        .join(RUN_STATE_DIR)
        .join(run_id)
        .join(module_id)
        .join("activity.jsonl");
    if !path.exists() {
        return json!({"entries": []});
    }
    let mut entries = Vec::new();
    if let Ok(content) = fs::read_to_string(&path) {
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Ok(v) = serde_json::from_str::<Value>(line) {
                entries.push(v);
            }
        }
    }
    json!({"entries": entries})
}

// --- module versioning / updates (spec 8.2) -------------------------------
fn parse_semver(s: &str) -> (u64, u64, u64) {
    let mut parts = s.trim().split('.').map(|p| p.parse::<u64>().unwrap_or(0));
    (
        parts.next().unwrap_or(0),
        parts.next().unwrap_or(0),
        parts.next().unwrap_or(0),
    )
}

/// Resolve the template remote's default branch (e.g. `master` or `main`).
/// The files repo historically used `master`, so hardcoding `main` silently
/// breaks version checks and module pulls. Detect it instead of assuming.
fn template_default_branch(template: &Path) -> String {
    if let Ok(out) = git_run(
        &["symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        template,
    ) {
        if let Some(branch) = out.trim().strip_prefix("origin/") {
            if !branch.is_empty() {
                return branch.to_string();
            }
        }
    }
    for cand in ["main", "master"] {
        if git_run(&["rev-parse", "--verify", &format!("origin/{cand}")], template).is_ok() {
            return cand.to_string();
        }
    }
    "main".to_string()
}

pub fn get_modules_version_status(install: &Path, id: &str, fetch: bool) -> Result<Value> {
    let project = project_dir(install, id);
    let template = install.join("template");
    let local = read_template_version(&project);
    if fetch {
        if let Err(e) = git_run(&["fetch", "origin"], &template) {
            append_eventlog(
                &project,
                "modules",
                "ERROR",
                &format!("fetch failed during version check: {e}"),
            );
        }
    }
    let branch = template_default_branch(&template);
    let remote_ref = format!("origin/{branch}:modules/template-version.txt");
    let remote = match git_run(&["show", &remote_ref], &template) {
        Ok(s) => s.trim().to_string(),
        Err(e) => {
            append_eventlog(
                &project,
                "modules",
                "ERROR",
                &format!("could not read remote module version ({remote_ref}): {e}"),
            );
            local.clone()
        }
    };
    let update_available = parse_semver(&remote) > parse_semver(&local);
    if fetch {
        let (log_status, detail) = if update_available {
            (
                "WARN",
                format!(
                    "app v{APP_VERSION} — modules v{local} installed — v{remote} available (update recommended)"
                ),
            )
        } else {
            (
                "SUCCESS",
                format!("app v{APP_VERSION} — modules v{local} up to date (remote v{remote})"),
            )
        };
        append_eventlog(&project, "modules", log_status, &detail);
    }
    Ok(json!({"local": local, "remote": remote, "updateAvailable": update_available}))
}

pub fn pull_project_modules(install: &Path, id: &str) -> Result<Value> {
    let project = project_dir(install, id);
    let template = install.join("template");
    if let Err(e) = git_run(&["fetch", "origin"], &template) {
        append_eventlog(&project, "modules", "ERROR", &format!("fetch failed: {e}"));
        return Err(e).context("fetch template");
    }
    let branch = template_default_branch(&template);
    let origin_ref = format!("origin/{branch}");
    if let Err(e) = git_run(&["merge", "--ff-only", &origin_ref], &template) {
        append_eventlog(
            &project,
            "modules",
            "ERROR",
            &format!("ff-merge {origin_ref} failed: {e}"),
        );
        return Err(e).context("ff-merge template");
    }
    let mut checkout = vec!["checkout", origin_ref.as_str(), "--"];
    checkout.extend_from_slice(SYNC_PATHS);
    if let Err(e) = git_run(&checkout, &project) {
        append_eventlog(
            &project,
            "modules",
            "ERROR",
            &format!("checkout synced paths failed: {e}"),
        );
        return Err(e).context("checkout synced paths into project");
    }
    let version = read_template_version(&project);
    append_eventlog(&project, "gui", "SUCCESS", &format!("pulled modules -> v{version}"));
    Ok(json!({"ok": true, "version": version}))
}

pub fn append_eventlog(project: &Path, scope: &str, status: &str, message: &str) {
    let ts = chrono::Local::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true);
    let line = format!("[{ts}] {scope} {status} — {message}\n");
    let _ = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(project.join(".eventlog"))
        .and_then(|mut f| std::io::Write::write_all(&mut f, line.as_bytes()));
}

#[allow(dead_code)] // surfaced to UI badges/logs; kept for completeness (spec 8.2)
pub fn template_version_of(install: &Path, id: &str) -> String {
    read_template_version(&project_dir(install, id))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn git_host_from_azure_url() {
        let url = "https://VikingAssistanceGroupAS@dev.azure.com/VikingAssistanceGroupAS/viking-assistance/_git/api";
        assert_eq!(git_host_from_url(url), Some("dev.azure.com".to_string()));
    }

    #[test]
    fn authenticated_https_url_injects_pat() {
        let url = "https://user@dev.azure.com/org/_git/repo";
        let out = authenticated_https_url(url, "pat123");
        assert_eq!(out, "https://user:pat123@dev.azure.com/org/_git/repo");
    }

    #[test]
    fn authenticated_https_url_encodes_special_chars_in_pat() {
        let url = "https://user@dev.azure.com/org/_git/repo";
        let out = authenticated_https_url(url, "pat@special");
        assert_eq!(out, "https://user:pat%40special@dev.azure.com/org/_git/repo");
    }

    #[test]
    fn https_repo_needs_pat_for_azure_and_embedded_user() {
        assert!(https_repo_needs_pat(
            "https://dev.azure.com/org/project/_git/repo"
        ));
        assert!(https_repo_needs_pat(
            "https://Org@dev.azure.com/x/_git/y"
        ));
        assert!(!https_repo_needs_pat("https://github.com/org/repo.git"));
    }

    #[test]
    fn git_batch_has_failures_on_partial_clone() {
        let results = json!({
            "results": [
                {"ok": false, "kind": "auth"},
                {"ok": true},
            ]
        });
        assert!(git_batch_has_failures(&results));
    }

    #[test]
    fn git_batch_failure_message_auth() {
        let results = json!({
            "results": [
                {"ok": false, "kind": "auth"},
                {"ok": true},
            ]
        });
        assert!(git_batch_has_failures(&results));
        assert!(git_batch_failure_message(&results).contains("username"));
    }

    #[test]
    fn resolve_git_auth_url_injects_meta_user() {
        let meta = "https://VikingAssistanceGroupAS@dev.azure.com/VikingAssistanceGroupAS/viking-assistance/_git/api";
        let remote = "https://dev.azure.com/VikingAssistanceGroupAS/viking-assistance/_git/api/";
        assert_eq!(
            resolve_git_auth_url(meta, remote),
            "https://VikingAssistanceGroupAS@dev.azure.com/VikingAssistanceGroupAS/viking-assistance/_git/api/"
        );
    }

    #[test]
    fn resolve_git_auth_url_keeps_remote_user() {
        let meta = "https://dev.azure.com/org/_git/a";
        let remote = "https://user@dev.azure.com/org/_git/a";
        assert_eq!(resolve_git_auth_url(meta, remote), remote);
    }

    #[test]
    fn module_description_parses_scope() {
        let tmp = tempfile::tempdir().unwrap();
        let skill = tmp.path().join("SKILL.md");
        fs::write(
            &skill,
            "# CI/CD Pipeline Audit\n\n## Scope\nEvaluate the quality of CI/CD configuration.\n\n## Inputs\n",
        )
        .unwrap();
        assert_eq!(
            module_description(&skill).as_deref(),
            Some("Evaluate the quality of CI/CD configuration.")
        );
    }

    #[test]
    fn packs_for_includes_test_coverage() {
        assert!(packs_for("test-coverage").contains(&"Engineering"));
    }

    fn write_minimal_meta(project: &Path, display_name: &str) {
        init_workspace_dirs(project).unwrap();
        write_meta(
            project,
            &ProjectMeta {
                display_name: display_name.to_string(),
                project_type: "both".to_string(),
                repos: vec![RepoEntry {
                    name: "fixture".to_string(),
                    url: String::new(),
                    branch: "main".to_string(),
                }],
                models: serde_json::Map::new(),
                selected_modules: Vec::new(),
                deleted: false,
            },
        )
        .unwrap();
    }

    fn init_git_repo(path: &Path, file: &str) {
        git_run(&["init", "-b", "main"], path).unwrap();
        fs::write(path.join(file), "content\n").unwrap();
        git_run(&["add", "."], path).unwrap();
        git_run(&["-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-m", "init"], path).unwrap();
    }

    #[test]
    fn module_selection_round_trip_filters_unknown() {
        let tmp = tempfile::tempdir().unwrap();
        let install = tmp.path();
        let project = install.join("projects/p1");
        write_minimal_meta(&project, "P1");
        fs::create_dir_all(project.join("modules/security-analysis")).unwrap();
        fs::write(
            project.join("modules/security-analysis/SKILL.md"),
            "# Security",
        )
        .unwrap();
        fs::create_dir_all(project.join("modules/accessibility-pass")).unwrap();
        fs::write(
            project.join("modules/accessibility-pass/SKILL.md"),
            "# A11y",
        )
        .unwrap();

        assert!(get_module_selection(install, "p1").is_empty());

        let saved = set_module_selection(
            install,
            "p1",
            vec![
                "security-analysis".to_string(),
                "unknown-module".to_string(),
                "accessibility-pass".to_string(),
            ],
        )
        .unwrap();
        assert_eq!(
            saved,
            vec!["security-analysis".to_string(), "accessibility-pass".to_string()]
        );
        assert_eq!(get_module_selection(install, "p1"), saved);
    }

    #[test]
    fn list_meta_dir_root_lists_flat_legacy_files() {
        let tmp = tempfile::tempdir().unwrap();
        let install = tmp.path();
        let project = install.join("projects/legacy");
        fs::create_dir_all(project.join("meta")).unwrap();
        write_minimal_meta(&project, "Legacy");
        fs::write(project.join("meta/requirements.md"), "# req").unwrap();

        let entries = list_meta_dir(install, "legacy", "").unwrap();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].kind, "file");
        assert_eq!(entries[0].name, "requirements.md");
    }

    #[test]
    fn list_meta_dir_nested_and_rejects_traversal() {
        let tmp = tempfile::tempdir().unwrap();
        let install = tmp.path();
        let project = install.join("projects/p1");
        fs::create_dir_all(project.join("meta/sub")).unwrap();
        write_minimal_meta(&project, "P1");
        fs::write(project.join("meta/sub/doc.md"), "x").unwrap();

        let sub = list_meta_dir(install, "p1", "sub").unwrap();
        assert_eq!(sub.len(), 1);
        assert_eq!(sub[0].name, "doc.md");

        assert!(list_meta_dir(install, "p1", "../..").is_err());
    }

    #[test]
    fn chatlog_roundtrip_and_malformed_reset() {
        let tmp = tempfile::tempdir().unwrap();
        let project = tmp.path().join("proj");
        fs::create_dir_all(&project).unwrap();
        assert!(load_chatlog(&project).is_empty());

        append_chatlog(&project, "user", "hi").unwrap();
        append_chatlog(&project, "assistant", "hello").unwrap();
        let msgs = load_chatlog(&project);
        assert_eq!(msgs.len(), 2);
        assert_eq!(msgs[0].role, "user");

        fs::write(project.join(CHATLOG_FILE), "not json").unwrap();
        let reset = load_chatlog(&project);
        assert!(reset.is_empty());
        assert!(project.join(format!("{CHATLOG_FILE}.bak")).exists());
    }

    #[test]
    fn repo_git_run_blocks_push() {
        let tmp = tempfile::tempdir().unwrap();
        init_git_repo(tmp.path(), "a.txt");
        let err = repo_git_run(&["push", "origin", "main"], tmp.path());
        assert!(err.is_err());
        assert!(repo_git_run(&["status"], tmp.path()).is_ok());
    }

    #[test]
    fn harden_readonly_repo_idempotent() {
        let tmp = tempfile::tempdir().unwrap();
        init_git_repo(tmp.path(), "a.txt");
        harden_readonly_repo(tmp.path()).unwrap();
        harden_readonly_repo(tmp.path()).unwrap();
        let hook = tmp.path().join(".git/hooks/pre-push");
        assert!(hook.exists());
    }

    #[test]
    fn legacy_project_fixture_loads() {
        let tmp = tempfile::tempdir().unwrap();
        let install = tmp.path();
        let project = install.join("projects/legacy");
        write_minimal_meta(&project, "Legacy");
        fs::write(project.join("meta/requirements.md"), "# req").unwrap();
        fs::write(project.join(".eventlog"), "[ts] gui START\n").unwrap();
        fs::create_dir_all(project.join("runs/old-run/m")).unwrap();
        fs::write(project.join("runs/old-run/m/analysis.md"), "ok").unwrap();
        fs::create_dir_all(project.join("modules/security-analysis")).unwrap();
        fs::write(
            project.join("modules/security-analysis/SKILL.md"),
            "# Security",
        )
        .unwrap();

        touch_project(&project);
        assert!(read_meta(&project).is_ok());
        assert_eq!(list_meta_dir(install, "legacy", "").unwrap().len(), 1);
        assert!(load_chatlog(&project).is_empty());
        let runs = list_runs(install, "legacy");
        assert!(!runs.is_empty());

        let index = crate::indexing::get_index(&project).unwrap();
        assert_eq!(index["items"].as_array().unwrap().len(), 0);
        assert!(!project.join(crate::indexing::INDEX_FILE).exists());
    }

    #[test]
    fn pull_repos_skips_uncloned() {
        let tmp = tempfile::tempdir().unwrap();
        let install = tmp.path();
        let project = install.join("projects/p1");
        write_minimal_meta(&project, "P1");
        let result = pull_repos(install, "p1").unwrap();
        let results = result["results"].as_array().unwrap();
        assert_eq!(results.len(), 1);
        assert!(!results[0]["ok"].as_bool().unwrap());

        let log = fs::read_to_string(project.join(".eventlog")).unwrap();
        assert!(log.contains("git ERROR"));
        assert!(log.contains("pull finished (0/1 ok)"));
    }

    #[test]
    fn delete_meta_entry_file_and_dir() {
        let tmp = tempfile::tempdir().unwrap();
        let install = tmp.path();
        let project = install.join("projects/p1");
        fs::create_dir_all(project.join("meta/empty")).unwrap();
        write_minimal_meta(&project, "P1");
        fs::write(project.join("meta/a.txt"), "x").unwrap();
        fs::write(project.join("meta/empty/nested.txt"), "y").unwrap();

        delete_meta_entry(install, "p1", "a.txt").unwrap();
        assert!(!project.join("meta/a.txt").exists());

        delete_meta_entry(install, "p1", "empty").unwrap();
        assert!(!project.join("meta/empty").exists());
    }

    #[test]
    fn upload_meta_files_to_subfolder() {
        let tmp = tempfile::tempdir().unwrap();
        let install = tmp.path();
        let project = install.join("projects/p1");
        write_minimal_meta(&project, "P1");
        fs::create_dir_all(project.join("meta/specs")).unwrap();
        let src = tmp.path().join("req.md");
        fs::write(&src, "# req").unwrap();
        let uploaded = upload_meta_files(install, "p1", &[src.to_string_lossy().to_string()], "specs").unwrap();
        assert_eq!(uploaded, vec!["specs/req.md"]);
        assert!(project.join("meta/specs/req.md").exists());
    }

    #[test]
    fn create_and_rename_meta_dir() {
        let tmp = tempfile::tempdir().unwrap();
        let install = tmp.path();
        let project = install.join("projects/p1");
        write_minimal_meta(&project, "P1");
        create_meta_dir(install, "p1", "specs").unwrap();
        assert!(project.join("meta/specs").is_dir());
        let renamed = rename_meta_entry(install, "p1", "specs", "requirements").unwrap();
        assert_eq!(renamed, "requirements");
        assert!(project.join("meta/requirements").is_dir());
    }

    #[test]
    fn upload_rejects_directories() {
        let tmp = tempfile::tempdir().unwrap();
        let install = tmp.path();
        let project = install.join("projects/p1");
        write_minimal_meta(&project, "P1");
        fs::create_dir_all(tmp.path().join("folder")).unwrap();
        let err = upload_meta_files(
            install,
            "p1",
            &[tmp.path().join("folder").to_string_lossy().to_string()],
            "",
        );
        assert!(err.is_err());
    }

    #[test]
    fn get_run_activity_parses_jsonl() {
        let tmp = tempfile::tempdir().unwrap();
        let install = tmp.path();
        let project = install.join("projects/p1");
        init_workspace_dirs(&project).unwrap();
        let activity_dir = project.join("runs/run-1/mod-a");
        fs::create_dir_all(&activity_dir).unwrap();
        fs::write(
            activity_dir.join("activity.jsonl"),
            r#"{"seq":1,"ts":"2025-01-01T00:00:00+00:00","kind":"turn","turn":1}
{"seq":2,"kind":"thinking","text":"hello"}
"#,
        )
        .unwrap();
        let out = get_run_activity(install, "p1", "run-1", "mod-a");
        let entries = out["entries"].as_array().unwrap();
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0]["kind"], "turn");
    }

    #[test]
    fn get_run_artifacts_includes_analysis_content() {
        let tmp = tempfile::tempdir().unwrap();
        let install = tmp.path();
        let project = install.join("projects/p1");
        init_workspace_dirs(&project).unwrap();
        let mod_dir = project.join("runs/run-1/security-analysis");
        fs::create_dir_all(&mod_dir).unwrap();
        fs::write(mod_dir.join("analysis.md"), "# Findings\n\nHello").unwrap();
        let out = get_run_artifacts(install, "p1", "run-1");
        let modules = out["modules"].as_array().unwrap();
        assert_eq!(modules.len(), 1);
        assert_eq!(modules[0]["hasAnalysis"], true);
        assert_eq!(modules[0]["analysis"], "# Findings\n\nHello");
    }

    #[test]
    fn get_run_state_reads_json() {
        let tmp = tempfile::tempdir().unwrap();
        let install = tmp.path();
        let project = install.join("projects/p1");
        init_workspace_dirs(&project).unwrap();
        let run_dir = project.join("runs/run-1");
        fs::create_dir_all(&run_dir).unwrap();
        fs::write(
            run_dir.join(".run-state.json"),
            r#"{"runId":"run-1","status":"complete","completedModules":["mod-a"]}"#,
        )
        .unwrap();
        let out = get_run_state(install, "p1", "run-1");
        assert_eq!(out["status"], "complete");
        assert_eq!(out["completedModules"][0], "mod-a");
    }

    #[test]
    fn get_run_artifacts_reports_has_activity() {
        let tmp = tempfile::tempdir().unwrap();
        let install = tmp.path();
        let project = install.join("projects/p1");
        init_workspace_dirs(&project).unwrap();
        let mod_dir = project.join("runs/run-1/workspace-index");
        fs::create_dir_all(&mod_dir).unwrap();
        fs::write(mod_dir.join("activity.jsonl"), "{}\n").unwrap();
        let out = get_run_artifacts(install, "p1", "run-1");
        let modules = out["modules"].as_array().unwrap();
        assert_eq!(modules.len(), 1);
        assert_eq!(modules[0]["hasActivity"], true);
    }
}
