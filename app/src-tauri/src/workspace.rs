//! Workspace, git worktrees, target-repo cloning, and module distribution (spec section 8).

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

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
            deleted: false,
        }
    }
}

// --- git helpers ----------------------------------------------------------
fn git_command() -> Command {
    let mut cmd = silent_command("git");
    cmd.env("GIT_TERMINAL_PROMPT", "0")
        .env("GCM_INTERACTIVE", "Never")
        .env("GIT_PAGER", "cat");
    cmd
}

fn git_run(args: &[&str], cwd: &Path) -> Result<String> {
    let output = git_command()
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
    "clone", "fetch", "pull", "merge", "status", "rev-parse", "branch", "log", "config",
];

fn repo_git_run(args: &[&str], cwd: &Path) -> Result<String> {
    let Some(cmd) = args.first() else {
        bail!("empty git args");
    };
    if !REPO_GIT_ALLOWED.contains(cmd) {
        bail!("git command '{cmd}' is not allowed on target repositories");
    }
    if args.iter().any(|a| *a == "push") {
        bail!("git push is disabled on target repositories");
    }
    git_run(args, cwd)
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
    let mut results = Vec::new();
    for repo in &meta.repos {
        let dest = repos_dir.join(&repo.name);
        if dest.join(".git").exists() {
            let _ = harden_readonly_repo(&dest);
            results.push(json!({"name": repo.name, "ok": true, "status": "already cloned"}));
            continue;
        }
        match clone_one(&repo.url, &repo.branch, &dest) {
            Ok(()) => {
                let _ = harden_readonly_repo(&dest);
                results.push(json!({"name": repo.name, "ok": true, "status": "cloned"}));
            }
            Err(e) => {
                let msg = e.to_string();
                let kind = classify_git_error(&msg);
                results.push(json!({"name": repo.name, "ok": false, "error": msg, "kind": kind}));
            }
        }
    }
    Ok(json!({"results": results}))
}

fn clone_one(url: &str, branch: &str, dest: &Path) -> Result<()> {
    let cwd = dest.parent().unwrap_or(Path::new("."));
    repo_git_run(
        &[
            "clone",
            "--depth",
            "1",
            "--branch",
            branch,
            url,
            dest.to_str().context("dest path")?,
        ],
        cwd,
    )
    .map(|_| ())
    .or_else(|_| {
        repo_git_run(
            &["clone", "--depth", "1", url, dest.to_str().unwrap()],
            cwd,
        )
        .map(|_| ())
    })
}

fn classify_git_error(stderr: &str) -> &'static str {
    let s = stderr.to_lowercase();
    if s.contains("authentication")
        || s.contains("permission denied")
        || s.contains("could not read")
        || s.contains("403")
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
    let mut results = Vec::new();
    for repo in &meta.repos {
        let dest = project.join("repos").join(&repo.name);
        if !dest.join(".git").exists() {
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
        let fetch_result = repo_git_run(&["fetch", "origin", &branch], &dest);
        match fetch_result {
            Ok(_) => {
                let merge_ref = format!("origin/{branch}");
                match repo_git_run(&["merge", "--ff-only", &merge_ref], &dest) {
                    Ok(_) => {
                        let head = repo_git_run(&["rev-parse", "--short", "HEAD"], &dest)
                            .ok()
                            .map(|s| s.trim().to_string());
                        results.push(json!({
                            "name": repo.name,
                            "ok": true,
                            "status": "updated",
                            "head": head,
                        }));
                    }
                    Err(e) => {
                        let msg = e.to_string();
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
                results.push(json!({
                    "name": repo.name,
                    "ok": false,
                    "error": msg,
                    "kind": classify_git_error(&msg),
                }));
            }
        }
    }
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

fn resolve_meta_path(project: &Path, rel_path: &str) -> Result<PathBuf> {
    let meta_root = project.join("meta");
    fs::create_dir_all(&meta_root).ok();
    let rel = rel_path.trim().trim_matches('/').replace('\\', "/");
    if rel.contains("..") {
        bail!("path traversal not allowed");
    }
    let target = if rel.is_empty() {
        meta_root.clone()
    } else {
        meta_root.join(&rel)
    };
    let meta_canonical = meta_root.canonicalize().unwrap_or(meta_root);
    let canonical = target.canonicalize().with_context(|| format!("resolve meta/{rel}"))?;
    if !canonical.starts_with(&meta_canonical) {
        bail!("path escapes meta directory");
    }
    Ok(canonical)
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

pub fn upload_meta_files(install: &Path, id: &str, sources: &[String]) -> Result<Vec<String>> {
    let project = project_dir(install, id);
    let meta_dir = project.join("meta");
    fs::create_dir_all(&meta_dir).ok();
    let mut copied = Vec::new();
    for src in sources {
        let src_path = Path::new(src);
        let Some(name) = src_path.file_name() else {
            continue;
        };
        let dest = meta_dir.join(name);
        if src_path.is_dir() {
            copy_dir_recursive(src_path, &dest)?;
            copied.push(name.to_string_lossy().to_string());
        } else {
            fs::copy(src_path, &dest).with_context(|| format!("copy {src}"))?;
            copied.push(name.to_string_lossy().to_string());
        }
    }
    Ok(copied)
}

fn copy_dir_recursive(src: &Path, dest: &Path) -> Result<()> {
    fs::create_dir_all(dest)?;
    for entry in fs::read_dir(src)? {
        let entry = entry?;
        let name = entry.file_name();
        let from = entry.path();
        let to = dest.join(&name);
        if from.is_dir() {
            copy_dir_recursive(&from, &to)?;
        } else {
            fs::copy(&from, &to).with_context(|| format!("copy {}", from.display()))?;
        }
    }
    Ok(())
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
        if fs::read_dir(&path)?.next().is_some() {
            bail!("directory is not empty");
        }
        fs::remove_dir(&path).context("delete meta dir")?;
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
                out.push(json!({"id": mid, "title": title, "packs": packs_for(&mid)}));
            }
        }
    }
    out.sort_by(|a, b| a["id"].as_str().unwrap_or("").cmp(b["id"].as_str().unwrap_or("")));
    out
}

fn module_title(skill: &Path) -> Option<String> {
    let content = fs::read_to_string(skill).ok()?;
    content
        .lines()
        .find(|l| l.starts_with("# "))
        .map(|l| l.trim_start_matches("# ").trim().to_string())
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
    let mut packs = Vec::new();
    let engineering = [
        "security-analysis",
        "accessibility-pass",
        "dependency-health",
        "cicd-pipeline-audit",
        "api-surface-analysis",
        "tech-debt-triage",
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

pub fn get_run_artifacts(install: &Path, id: &str, run_id: &str) -> Value {
    let run_dir = project_dir(install, id).join(RUN_STATE_DIR).join(run_id);
    let mut modules = Vec::new();
    if let Ok(entries) = fs::read_dir(&run_dir) {
        for e in entries.flatten() {
            if !e.path().is_dir() {
                continue;
            }
            let mid = e.file_name().to_string_lossy().to_string();
            let analysis = e.path().join("analysis.md").exists();
            let tasks_path = e.path().join("tasks.json");
            let tasks = fs::read_to_string(&tasks_path)
                .ok()
                .and_then(|s| serde_json::from_str::<Value>(&s).ok());
            modules.push(json!({
                "moduleId": mid,
                "hasAnalysis": analysis,
                "tasks": tasks,
            }));
        }
    }
    let summary = fs::read_to_string(run_dir.join("run-summary.md")).ok();
    json!({"runId": run_id, "modules": modules, "summary": summary})
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

pub fn get_modules_version_status(install: &Path, id: &str, fetch: bool) -> Result<Value> {
    let project = project_dir(install, id);
    let template = install.join("template");
    let local = read_template_version(&project);
    if fetch {
        let _ = git_run(&["fetch", "origin", "main"], &template);
    }
    let remote = git_run(&["show", "origin/main:modules/template-version.txt"], &template)
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|_| local.clone());
    let update_available = parse_semver(&remote) > parse_semver(&local);
    Ok(json!({"local": local, "remote": remote, "updateAvailable": update_available}))
}

pub fn pull_project_modules(install: &Path, id: &str) -> Result<Value> {
    let project = project_dir(install, id);
    let template = install.join("template");
    git_run(&["fetch", "origin", "main"], &template).context("fetch template")?;
    git_run(&["merge", "--ff-only", "origin/main"], &template).context("ff-merge template")?;
    let mut checkout = vec!["checkout", "origin/main", "--"];
    checkout.extend_from_slice(SYNC_PATHS);
    git_run(&checkout, &project).context("checkout synced paths into project")?;
    let version = read_template_version(&project);
    append_eventlog(&project, "gui", "SUCCESS", &format!("pulled modules -> v{version}"));
    Ok(json!({"ok": true, "version": version}))
}

fn append_eventlog(project: &Path, scope: &str, status: &str, message: &str) {
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
    }

    #[test]
    fn delete_meta_entry_file_and_empty_dir() {
        let tmp = tempfile::tempdir().unwrap();
        let install = tmp.path();
        let project = install.join("projects/p1");
        fs::create_dir_all(project.join("meta/empty")).unwrap();
        write_minimal_meta(&project, "P1");
        fs::write(project.join("meta/a.txt"), "x").unwrap();

        delete_meta_entry(install, "p1", "a.txt").unwrap();
        assert!(!project.join("meta/a.txt").exists());

        delete_meta_entry(install, "p1", "empty").unwrap();
        assert!(!project.join("meta/empty").exists());
    }
}
