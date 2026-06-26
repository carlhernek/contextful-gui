//! Contextful Tauri core: command surface + app wiring (spec section 10).

mod git_credentials;
mod github_cli;
mod indexing;
mod jobs;
mod prereqs;
mod secrets;
mod settings;
mod sidecar;
mod workspace;

use std::path::PathBuf;
use std::sync::Arc;

use jobs::{busy_error, JobKind, JobManager};
use serde_json::{json, Map, Value};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_dialog::DialogExt;

use sidecar::SidecarManager;

const APP_VERSION: &str = env!("CARGO_PKG_VERSION");

pub struct AppState {
    pub sidecar: Arc<SidecarManager>,
    pub jobs: Arc<JobManager>,
}

type CmdResult<T> = Result<T, String>;

fn err<E: std::fmt::Display>(e: E) -> String {
    e.to_string()
}

fn log_project_error(project: &std::path::Path, scope: &str, message: &str) {
    workspace::append_eventlog(project, scope, "ERROR", message);
}

fn reject_job_busy(app: &AppHandle, project_id: &str, operation: &str, busy: jobs::JobBusy) -> String {
    if let Ok(install) = install_path(app) {
        let project = workspace::project_dir(&install, project_id);
        let msg = busy_error(busy);
        log_project_error(
            &project,
            "job",
            &format!("{operation} rejected — {msg}"),
        );
        msg
    } else {
        busy_error(busy)
    }
}

fn install_path(app: &AppHandle) -> CmdResult<PathBuf> {
    let s = settings::load_settings(app).map_err(err)?;
    s.install_path
        .map(PathBuf::from)
        .ok_or_else(|| "install path not set".to_string())
}

// ===== prerequisites & setup ==============================================
#[tauri::command]
fn check_prereqs() -> prereqs::PrereqStatus {
    prereqs::check()
}

#[tauri::command]
async fn install_prereqs() -> CmdResult<String> {
    tauri::async_runtime::spawn_blocking(prereqs::install_prereqs)
        .await
        .map_err(err)
}

#[tauri::command]
fn default_install_folder() -> String {
    settings::default_install_folder().to_string_lossy().to_string()
}

#[tauri::command]
async fn pick_install_folder(app: AppHandle) -> CmdResult<Option<String>> {
    let folder = app.dialog().file().blocking_pick_folder();
    Ok(folder.map(|p| p.to_string()))
}

#[tauri::command]
fn set_install_path(app: AppHandle, path: String) -> CmdResult<Value> {
    settings::set_install_path(&app, &path).map_err(err)?;
    Ok(settings::settings_json(&app))
}

#[tauri::command]
async fn setup_template(app: AppHandle) -> CmdResult<Value> {
    let install = install_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || workspace::setup_template(&install))
        .await
        .map_err(err)?
        .map_err(err)
}

#[tauri::command]
fn get_setup_status(app: AppHandle) -> Value {
    let masked = secrets::masked_api_key().ok().flatten();
    let s = settings::load_settings(&app).unwrap_or_default();
    let template_ready = s
        .install_path
        .as_ref()
        .map(|p| PathBuf::from(p).join("template").join(".git").exists())
        .unwrap_or(false);
    json!({
        "hasApiKey": masked.is_some(),
        "maskedApiKey": masked,
        "templateReady": template_ready,
        "installPath": s.install_path,
        "activeProject": s.active_project,
    })
}

// ===== secrets ============================================================
#[tauri::command]
fn set_api_key(key: String) -> CmdResult<()> {
    secrets::save_api_key(&key).map_err(err)
}

#[tauri::command]
fn clear_api_key() -> CmdResult<()> {
    secrets::delete_api_key().map_err(err)
}

#[tauri::command]
fn stored_api_key_masked() -> CmdResult<Option<String>> {
    secrets::masked_api_key().map_err(err)
}

// ===== git credentials (HTTPS PAT per host) ===============================
#[tauri::command]
fn list_git_credential_hosts(app: AppHandle, id: String) -> CmdResult<Value> {
    let install = install_path(&app)?;
    let meta = workspace::read_meta(&workspace::project_dir(&install, &id)).map_err(err)?;
    let from_repos = workspace::unique_git_hosts(meta.repos.iter().map(|r| r.url.as_str()));
    let s = settings::load_settings(&app).unwrap_or_default();
    let mut hosts: Vec<String> = s.git_credential_hosts.clone();
    for h in from_repos {
        if !hosts.iter().any(|x| x == &h) {
            hosts.push(h);
        }
    }
    hosts.sort();
    let configured: Vec<Value> = hosts
        .iter()
        .map(|h| {
            json!({
                "host": h,
                "configured": git_credentials::load(h).ok().flatten().is_some(),
                "masked": git_credentials::masked(h).ok().flatten(),
                "username": git_credentials::load_user(h).ok().flatten(),
            })
        })
        .collect();
    Ok(json!({ "hosts": configured }))
}

#[tauri::command]
fn set_git_credential(
    app: AppHandle,
    host: String,
    token: String,
    username: Option<String>,
) -> CmdResult<()> {
    let host = git_credentials::normalize_host(&host);
    git_credentials::save(&host, &token).map_err(err)?;
    if let Some(user) = username {
        git_credentials::save_user(&host, &user).map_err(err)?;
    }
    let mut s = settings::load_settings(&app).unwrap_or_default();
    if !s.git_credential_hosts.iter().any(|h| h == &host) {
        s.git_credential_hosts.push(host);
        s.git_credential_hosts.sort();
        settings::save_settings(&app, &s).map_err(err)?;
    }
    Ok(())
}

#[tauri::command]
fn clear_git_credential(app: AppHandle, host: String) -> CmdResult<()> {
    let host = git_credentials::normalize_host(&host);
    git_credentials::delete(&host).map_err(err)?;
    let mut s = settings::load_settings(&app).unwrap_or_default();
    s.git_credential_hosts.retain(|h| h != &host);
    settings::save_settings(&app, &s).map_err(err)
}

// ===== github cli (preferred for github.com HTTPS) =======================
#[tauri::command]
async fn github_cli_status() -> github_cli::GithubCliStatus {
    tauri::async_runtime::spawn_blocking(github_cli::status)
        .await
        .unwrap_or_default()
}

#[tauri::command]
async fn setup_github_cli_git() -> CmdResult<String> {
    tauri::async_runtime::spawn_blocking(github_cli::setup_git)
        .await
        .map_err(err)?
}

// ===== supabase (Management API connections) ==============================
#[tauri::command]
fn set_supabase_token(token: String) -> CmdResult<()> {
    secrets::save_supabase_pat(&token).map_err(err)
}

#[tauri::command]
fn clear_supabase_token() -> CmdResult<()> {
    secrets::delete_supabase_pat().map_err(err)
}

#[tauri::command]
fn stored_supabase_token_masked() -> CmdResult<Option<String>> {
    secrets::masked_supabase_pat().map_err(err)
}

/// Load the stored PAT and ask the sidecar for the account's project list.
#[tauri::command]
async fn list_supabase_projects(
    app: AppHandle,
    state: State<'_, AppState>,
    id: String,
) -> CmdResult<Value> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    let pat = secrets::load_supabase_pat()
        .map_err(err)?
        .unwrap_or_default();
    if pat.trim().is_empty() {
        return Err("no Supabase token configured".into());
    }
    let workspace = project.to_string_lossy().to_string();
    workspace::append_eventlog(&project, "supabase", "START", "listing account projects");
    let result = rpc(
        app,
        state.sidecar.clone(),
        "list_supabase_projects",
        json!({ "pat": pat, "workspace": workspace }),
    )
    .await;
    match &result {
        Err(e) => log_project_error(&project, "supabase", &format!("list projects failed — {e}")),
        Ok(v) => {
            let count = v.get("projects").and_then(|p| p.as_array()).map(|a| a.len()).unwrap_or(0);
            workspace::append_eventlog(
                &project,
                "supabase",
                "SUCCESS",
                &format!("listed {count} account project(s)"),
            );
        }
    }
    result
}

#[tauri::command]
fn add_supabase(
    app: AppHandle,
    id: String,
    name: String,
    project_ref: String,
    region: Option<String>,
) -> CmdResult<()> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    workspace::add_supabase(&install, &id, &name, &project_ref, region.as_deref()).map_err(|e| {
        log_project_error(&project, "supabase", &format!("add connection {name} failed — {e}"));
        err(e)
    })?;
    workspace::append_eventlog(
        &project,
        "supabase",
        "SUCCESS",
        &format!("added connection {name} ({project_ref})"),
    );
    Ok(())
}

#[tauri::command]
fn remove_supabase(app: AppHandle, id: String, project_ref: String) -> CmdResult<()> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    workspace::remove_supabase(&install, &id, &project_ref).map_err(|e| {
        log_project_error(&project, "supabase", &format!("remove connection {project_ref} failed — {e}"));
        err(e)
    })?;
    workspace::append_eventlog(
        &project,
        "supabase",
        "SUCCESS",
        &format!("removed connection {project_ref}"),
    );
    Ok(())
}

#[tauri::command]
fn list_supabase(app: AppHandle, id: String) -> CmdResult<Vec<Value>> {
    let install = install_path(&app)?;
    workspace::list_supabase(&install, &id).map_err(err)
}

/// Snapshot one connection's configuration via the Management API (sidecar).
#[tauri::command]
async fn snapshot_supabase(
    app: AppHandle,
    state: State<'_, AppState>,
    id: String,
    project_ref: String,
) -> CmdResult<Value> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    let pat = secrets::load_supabase_pat()
        .map_err(err)?
        .unwrap_or_default();
    if pat.trim().is_empty() {
        return Err("no Supabase token configured".into());
    }
    let meta = workspace::read_meta(&project).map_err(err)?;
    let conn = meta
        .supabase
        .iter()
        .find(|c| c.project_ref == project_ref)
        .cloned()
        .ok_or_else(|| "no such Supabase connection".to_string())?;
    let subdir = workspace::supabase_subdir(&conn.name);
    let workspace = project.to_string_lossy().to_string();

    let guard = state
        .jobs
        .try_begin(
            &app,
            &project,
            JobKind::Snapshot,
            &id,
            &format!("Snapshotting {}", conn.name),
        )
        .map_err(|busy| reject_job_busy(&app, &id, "snapshot", busy))?;

    let result = rpc(
        app.clone(),
        state.sidecar.clone(),
        "snapshot_supabase",
        json!({
            "pat": pat,
            "projectRef": project_ref,
            "name": conn.name,
            "region": conn.region,
            "status": Value::Null,
            "workspace": workspace,
            "subdir": subdir,
        }),
    )
    .await;

    match &result {
        Err(e) => {
            guard.fail_with("supabase", &format!("snapshot {} failed — {e}", conn.name));
        }
        Ok(_) => {
            let _ = workspace::mark_supabase_snapshot(
                &install,
                &id,
                &project_ref,
                conn.region.as_deref(),
            );
            workspace::append_eventlog(
                &project,
                "supabase",
                "SUCCESS",
                &format!("snapshot written for {} ({project_ref})", conn.name),
            );
        }
    }
    drop(guard);
    result
}

// ===== audio transcription ================================================
const AUDIO_PICK_EXTENSIONS: &[&str] =
    &["mp3", "wav", "m4a", "ogg", "flac", "aac", "aiff", "webm"];

/// Pick audio files via a native dialog and copy them into `meta/audio/`.
#[tauri::command]
async fn add_audio_files(app: AppHandle, id: String) -> CmdResult<Vec<String>> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    let picked = app
        .dialog()
        .file()
        .add_filter("Audio", AUDIO_PICK_EXTENSIONS)
        .blocking_pick_files();
    let Some(files) = picked else {
        return Ok(Vec::new());
    };
    let sources: Vec<String> = files.into_iter().map(|p| p.to_string()).collect();
    if sources.is_empty() {
        return Ok(Vec::new());
    }
    let uploaded = workspace::upload_meta_files(&install, &id, &sources, "audio").map_err(|e| {
        log_project_error(&project, "transcription", &format!("add audio failed — {e}"));
        err(e)
    })?;
    workspace::append_eventlog(
        &project,
        "transcription",
        "SUCCESS",
        &format!("added {} audio file(s): {}", uploaded.len(), uploaded.join(", ")),
    );
    Ok(uploaded)
}

/// Copy drag-and-dropped audio files into `meta/audio/`.
///
/// Filters dropped paths to known audio extensions, skips folders, and never
/// triggers transcription — transcripts are produced only on an explicit
/// transcribe/module run.
#[tauri::command]
async fn add_audio_paths(app: AppHandle, id: String, paths: Vec<String>) -> CmdResult<Vec<String>> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    let sources: Vec<String> = paths
        .into_iter()
        .filter(|p| {
            std::path::Path::new(p)
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| AUDIO_PICK_EXTENSIONS.contains(&e.to_ascii_lowercase().as_str()))
                .unwrap_or(false)
        })
        .collect();
    if sources.is_empty() {
        return Ok(Vec::new());
    }
    let uploaded = workspace::upload_meta_files(&install, &id, &sources, "audio").map_err(|e| {
        log_project_error(&project, "transcription", &format!("add audio failed — {e}"));
        err(e)
    })?;
    workspace::append_eventlog(
        &project,
        "transcription",
        "SUCCESS",
        &format!("added {} audio file(s): {}", uploaded.len(), uploaded.join(", ")),
    );
    Ok(uploaded)
}

/// List audio meta documents and their transcription status (filesystem read).
#[tauri::command]
async fn list_audio(app: AppHandle, state: State<'_, AppState>, id: String) -> CmdResult<Value> {
    let install = install_path(&app)?;
    let workspace = workspace::project_dir(&install, &id)
        .to_string_lossy()
        .to_string();
    rpc(app, state.sidecar.clone(), "list_audio", json!({ "workspace": workspace })).await
}

/// Transcribe pending audio meta documents via OpenRouter STT (sidecar), then index.
#[tauri::command]
async fn transcribe_audio(app: AppHandle, state: State<'_, AppState>, id: String) -> CmdResult<Value> {
    ensure_configured(&app, &state, Some(&id)).await?;
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    let workspace = project.to_string_lossy().to_string();

    let guard = state
        .jobs
        .try_begin(&app, &project, JobKind::Transcribe, &id, "Transcribing audio")
        .map_err(|busy| reject_job_busy(&app, &id, "transcribe", busy))?;

    workspace::append_eventlog(&project, "transcription", "START", "transcribe pending audio");

    let result = rpc(
        app.clone(),
        state.sidecar.clone(),
        "transcribe_audio",
        json!({ "workspace": workspace }),
    )
    .await;

    match &result {
        Err(e) => {
            guard.fail_with("transcription", &format!("transcription failed — {e}"));
        }
        Ok(_) => {
            workspace::append_eventlog(&project, "transcription", "SUCCESS", "transcription complete");
        }
    }
    drop(guard);
    result
}

// ===== settings & models ==================================================
#[tauri::command]
fn get_settings(app: AppHandle) -> Value {
    settings::settings_json(&app)
}

#[tauri::command]
fn set_models(app: AppHandle, project_id: String, models: Map<String, Value>) -> CmdResult<()> {
    let install = install_path(&app)?;
    workspace::set_models(&install, &project_id, models).map_err(err)
}

#[tauri::command]
async fn list_models(app: AppHandle, state: State<'_, AppState>) -> CmdResult<Value> {
    ensure_configured(&app, &state, None).await?;
    rpc(app, state.sidecar.clone(), "list_models", json!({})).await
}

// ===== projects ===========================================================
#[tauri::command]
fn suggest_project_id(app: AppHandle, display_name: String) -> CmdResult<String> {
    let install = install_path(&app)?;
    Ok(workspace::suggest_project_id(&install, &display_name))
}

#[tauri::command]
fn list_projects(app: AppHandle) -> CmdResult<Vec<Value>> {
    let install = install_path(&app)?;
    Ok(workspace::list_projects(&install))
}

#[tauri::command]
async fn create_project(app: AppHandle, id: String, display_name: String) -> CmdResult<String> {
    let install = install_path(&app)?;
    let project = tauri::async_runtime::spawn_blocking(move || {
        workspace::create_project(&install, &id, &display_name)
    })
    .await
    .map_err(err)?
    .map_err(err)?;
    Ok(project.to_string_lossy().to_string())
}

#[tauri::command]
fn rename_project(app: AppHandle, id: String, display_name: String) -> CmdResult<()> {
    let install = install_path(&app)?;
    workspace::rename_project(&install, &id, &display_name).map_err(err)
}

#[tauri::command]
fn delete_project(app: AppHandle, id: String) -> CmdResult<()> {
    let install = install_path(&app)?;
    workspace::delete_project(&install, &id).map_err(err)
}

#[tauri::command]
fn set_active_project(app: AppHandle, id: Option<String>) -> CmdResult<Value> {
    settings::set_active_project(&app, id.as_deref()).map_err(err)?;
    Ok(settings::settings_json(&app))
}

#[tauri::command]
fn set_project_type(app: AppHandle, id: String, project_type: String) -> CmdResult<()> {
    let install = install_path(&app)?;
    workspace::set_project_type(&install, &id, &project_type).map_err(err)
}

// ===== target repos =======================================================
#[tauri::command]
fn add_repo(app: AppHandle, id: String, name: String, url: String, branch: String) -> CmdResult<()> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    workspace::add_repo(&install, &id, &name, &url, &branch).map_err(|e| {
        log_project_error(&project, "git", &format!("add repo {name} failed — {e}"));
        err(e)
    })
}

#[tauri::command]
fn remove_repo(app: AppHandle, id: String, name: String) -> CmdResult<()> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    workspace::remove_repo(&install, &id, &name).map_err(|e| {
        log_project_error(&project, "git", &format!("remove repo {name} failed — {e}"));
        err(e)
    })
}

#[tauri::command]
async fn clone_repos(app: AppHandle, state: State<'_, AppState>, id: String) -> CmdResult<Value> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    let guard = state
        .jobs
        .try_begin(&app, &project, JobKind::Clone, &id, "Cloning repositories")
        .map_err(|busy| reject_job_busy(&app, &id, "clone", busy))?;
    let clone_result = tauri::async_runtime::spawn_blocking(move || workspace::clone_repos(&install, &id))
        .await
        .map_err(err)?
        .map_err(err);
    if let Err(ref e) = clone_result {
        guard.fail_with("git", &format!("clone failed — {e}"));
    } else if let Ok(ref val) = clone_result {
        if workspace::git_batch_has_failures(val) {
            guard.fail_with("git", &workspace::git_batch_failure_message(val));
        }
    }
    drop(guard);
    clone_result
}

#[tauri::command]
fn list_repos(app: AppHandle, id: String) -> CmdResult<Vec<Value>> {
    let install = install_path(&app)?;
    workspace::list_repos(&install, &id).map_err(err)
}

#[tauri::command]
async fn pull_repos(app: AppHandle, state: State<'_, AppState>, id: String) -> CmdResult<Value> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    let guard = state
        .jobs
        .try_begin(&app, &project, JobKind::Pull, &id, "Pulling repositories")
        .map_err(|busy| reject_job_busy(&app, &id, "pull", busy))?;
    let pull_result = tauri::async_runtime::spawn_blocking(move || workspace::pull_repos(&install, &id))
        .await
        .map_err(err)?
        .map_err(err);
    if let Err(ref e) = pull_result {
        guard.fail_with("git", &format!("pull failed — {e}"));
    } else if let Ok(ref val) = pull_result {
        if workspace::git_batch_has_failures(val) {
            guard.fail_with("git", &workspace::git_batch_failure_message(val));
        }
    }
    drop(guard);
    pull_result
}

// ===== meta docs ==========================================================
#[tauri::command]
async fn upload_meta_files(
    app: AppHandle,
    _state: State<'_, AppState>,
    id: String,
    sources: Vec<String>,
    dest_path: Option<String>,
) -> CmdResult<Vec<String>> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    let dest = dest_path.unwrap_or_default();
    let uploaded =
        workspace::upload_meta_files(&install, &id, &sources, &dest).map_err(|e| {
            log_project_error(&project, "meta", &format!("upload failed — {e}"));
            err(e)
        })?;
    workspace::append_eventlog(
        &project,
        "meta",
        "SUCCESS",
        &format!("uploaded {} file(s): {}", uploaded.len(), uploaded.join(", ")),
    );
    Ok(uploaded)
}

#[tauri::command]
fn create_meta_dir(app: AppHandle, id: String, path: String) -> CmdResult<()> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    workspace::create_meta_dir(&install, &id, &path).map_err(|e| {
        log_project_error(&project, "meta", &format!("create folder {path} failed — {e}"));
        err(e)
    })?;
    workspace::append_eventlog(&project, "meta", "SUCCESS", &format!("created folder {path}"));
    Ok(())
}

#[tauri::command]
fn rename_meta_entry(app: AppHandle, id: String, path: String, new_name: String) -> CmdResult<String> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    let new_path = workspace::rename_meta_entry(&install, &id, &path, &new_name).map_err(|e| {
        log_project_error(&project, "meta", &format!("rename {path} failed — {e}"));
        err(e)
    })?;
    workspace::append_eventlog(
        &project,
        "meta",
        "SUCCESS",
        &format!("renamed {path} -> {new_path}"),
    );
    Ok(new_path)
}

#[tauri::command]
fn move_meta_entry(
    app: AppHandle,
    id: String,
    path: String,
    dest_path: String,
) -> CmdResult<String> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    let new_path =
        workspace::move_meta_entry(&install, &id, &path, &dest_path).map_err(|e| {
            log_project_error(&project, "meta", &format!("move {path} failed — {e}"));
            err(e)
        })?;
    workspace::append_eventlog(
        &project,
        "meta",
        "SUCCESS",
        &format!("moved {path} -> {new_path}"),
    );
    Ok(new_path)
}

#[tauri::command]
fn list_meta_dir(app: AppHandle, id: String, rel_path: Option<String>) -> CmdResult<Vec<workspace::MetaEntry>> {
    let install = install_path(&app)?;
    workspace::list_meta_dir(&install, &id, rel_path.as_deref().unwrap_or("")).map_err(err)
}

#[tauri::command]
async fn delete_meta_entry(
    app: AppHandle,
    _state: State<'_, AppState>,
    id: String,
    path: String,
) -> CmdResult<()> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    workspace::delete_meta_entry(&install, &id, &path).map_err(|e| {
        log_project_error(&project, "meta", &format!("delete {path} failed — {e}"));
        err(e)
    })?;
    workspace::append_eventlog(&project, "meta", "SUCCESS", &format!("deleted {path}"));
    Ok(())
}

#[tauri::command]
fn get_chatlog(app: AppHandle, id: String) -> CmdResult<Vec<workspace::ChatMessage>> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    workspace::touch_project(&project);
    Ok(workspace::load_chatlog(&project))
}

#[tauri::command]
fn list_meta_files(app: AppHandle, id: String) -> CmdResult<Vec<Value>> {
    let install = install_path(&app)?;
    Ok(workspace::list_meta_files(&install, &id))
}

#[tauri::command]
fn delete_meta_file(app: AppHandle, id: String, name: String) -> CmdResult<()> {
    let install = install_path(&app)?;
    workspace::delete_meta_file(&install, &id, &name).map_err(err)
}

// ===== modules ============================================================
#[tauri::command]
fn list_modules(app: AppHandle, id: String) -> CmdResult<Vec<Value>> {
    let install = install_path(&app)?;
    Ok(workspace::list_modules(&install, &id))
}

#[tauri::command]
fn get_module_suggestions(app: AppHandle, id: String) -> CmdResult<Vec<String>> {
    let install = install_path(&app)?;
    Ok(workspace::get_module_suggestions(&install, &id))
}

#[tauri::command]
fn get_module_selection(app: AppHandle, id: String) -> CmdResult<Vec<String>> {
    let install = install_path(&app)?;
    Ok(workspace::get_module_selection(&install, &id))
}

#[tauri::command]
fn set_module_selection(app: AppHandle, id: String, modules: Vec<String>) -> CmdResult<Vec<String>> {
    let install = install_path(&app)?;
    workspace::set_module_selection(&install, &id, modules).map_err(err)
}

// ===== logs / runs / artifacts ============================================
#[tauri::command]
fn get_event_log(app: AppHandle, id: String) -> CmdResult<String> {
    let install = install_path(&app)?;
    Ok(workspace::get_event_log(&install, &id))
}

#[tauri::command]
fn get_run_log(app: AppHandle, id: String, run_id: String) -> CmdResult<String> {
    let install = install_path(&app)?;
    Ok(workspace::get_run_log(&install, &id, &run_id))
}

#[tauri::command]
fn list_runs(app: AppHandle, id: String) -> CmdResult<Vec<Value>> {
    let install = install_path(&app)?;
    Ok(workspace::list_runs(&install, &id))
}

#[tauri::command]
fn get_run_artifacts(app: AppHandle, id: String, run_id: String) -> CmdResult<Value> {
    let install = install_path(&app)?;
    Ok(workspace::get_run_artifacts(&install, &id, &run_id))
}

#[tauri::command]
fn get_run_activity(
    app: AppHandle,
    id: String,
    run_id: String,
    module_id: String,
) -> CmdResult<Value> {
    let install = install_path(&app)?;
    Ok(workspace::get_run_activity(&install, &id, &run_id, &module_id))
}

#[tauri::command]
async fn get_run_state(app: AppHandle, id: String, run_id: String) -> CmdResult<Value> {
    let install = install_path(&app)?;
    Ok(tauri::async_runtime::spawn_blocking(move || workspace::get_run_state(&install, &id, &run_id))
        .await
        .map_err(err)?)
}

// ===== module versioning ==================================================
#[tauri::command]
async fn get_modules_version_status(app: AppHandle, id: String, fetch: bool) -> CmdResult<Value> {
    let install = install_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        workspace::get_modules_version_status(&install, &id, fetch)
    })
    .await
    .map_err(err)?
    .map_err(err)
}

#[tauri::command]
async fn pull_project_modules(app: AppHandle, id: String) -> CmdResult<Value> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    tauri::async_runtime::spawn_blocking(move || workspace::pull_project_modules(&install, &id))
        .await
        .map_err(err)?
        .map_err(|e| {
            log_project_error(&project, "gui", &format!("pull modules failed — {e}"));
            err(e)
        })
}

// ===== sidecar-backed operations ==========================================
fn project_workspace(app: &AppHandle, id: &str) -> CmdResult<String> {
    let install = install_path(app)?;
    Ok(workspace::project_dir(&install, id).to_string_lossy().to_string())
}

async fn rpc(
    app: AppHandle,
    mgr: Arc<SidecarManager>,
    method: &str,
    params: Value,
) -> CmdResult<Value> {
    let method = method.to_string();
    tauri::async_runtime::spawn_blocking(move || {
        mgr.request(&app, &method, params).map_err(|e| e.to_string())
    })
    .await
    .map_err(err)?
}

/// Configure-before-use: send the keychain key + project models to the sidecar.
async fn ensure_configured(
    app: &AppHandle,
    state: &State<'_, AppState>,
    project_id: Option<&str>,
) -> CmdResult<()> {
    let key = secrets::load_api_key().map_err(err)?.unwrap_or_default();
    if key.is_empty() {
        return Err("no API key configured".into());
    }
    let mut models = Map::new();
    if let Some(pid) = project_id {
        let install = install_path(app)?;
        if let Ok(meta) = workspace::read_meta(&workspace::project_dir(&install, pid)) {
            models = meta.models;
        }
    }
    rpc(app.clone(), state.sidecar.clone(), "configure",
        json!({"api_key": key, "models": models})).await?;
    Ok(())
}

fn project_dir(app: &AppHandle, project_id: &str) -> CmdResult<PathBuf> {
    let install = install_path(app)?;
    Ok(workspace::project_dir(&install, project_id))
}

#[tauri::command]
fn list_jobs(state: State<'_, AppState>) -> Vec<jobs::Job> {
    state.jobs.snapshot()
}

#[tauri::command]
fn stop_job(app: AppHandle, state: State<'_, AppState>) -> CmdResult<()> {
    if let Some(job) = state.jobs.current() {
        if let Ok(project) = project_dir(&app, &job.project_id) {
            workspace::append_eventlog(
                &project,
                "job",
                "CANCELLED",
                &format!("stop requested — {} ({})", job.label, job.kind.as_str()),
            );
        }
        state.jobs.mark_cancelled_logged();
    }
    state.sidecar.cancel();
    Ok(())
}

#[tauri::command]
async fn get_index(app: AppHandle, id: String) -> CmdResult<Value> {
    let install = install_path(&app)?;
    indexing::get_index(&workspace::project_dir(&install, &id)).map_err(err)
}

#[tauri::command]
async fn read_index_item(app: AppHandle, id: String, item_id: String) -> CmdResult<Value> {
    let install = install_path(&app)?;
    match indexing::read_index_item(&workspace::project_dir(&install, &id), &item_id).map_err(err)? {
        Some(item) => Ok(item),
        None => Err(format!("index item not found: {item_id}")),
    }
}

#[tauri::command]
async fn set_index_annotation(
    app: AppHandle,
    id: String,
    item_id: String,
    description: String,
    keywords: Vec<String>,
) -> CmdResult<Value> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    let ann = indexing::set_annotation(&project, &item_id, &description, &keywords).map_err(|e| {
        log_project_error(
            &project,
            "index",
            &format!("annotation save {item_id} failed — {e}"),
        );
        err(e)
    })?;
    let kw = if keywords.is_empty() {
        "(none)".to_string()
    } else {
        keywords.join(", ")
    };
    let desc_preview = if description.trim().is_empty() {
        "(empty)".to_string()
    } else {
        description.trim().chars().take(120).collect()
    };
    workspace::append_eventlog(
        &project,
        "index",
        "SUCCESS",
        &format!("annotation saved {item_id} keywords=[{kw}] description=\"{desc_preview}\""),
    );
    Ok(json!(ann))
}

#[tauri::command]
async fn configure_sidecar(app: AppHandle, state: State<'_, AppState>, project_id: Option<String>) -> CmdResult<()> {
    ensure_configured(&app, &state, project_id.as_deref()).await
}

#[tauri::command]
async fn sidecar_health(app: AppHandle, state: State<'_, AppState>) -> CmdResult<Value> {
    ensure_configured(&app, &state, None).await?;
    rpc(app, state.sidecar.clone(), "health", json!({})).await
}

#[tauri::command]
async fn send_chat(app: AppHandle, state: State<'_, AppState>, id: String, message: String) -> CmdResult<Value> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    if let Err(e) = ensure_configured(&app, &state, Some(&id)).await {
        log_project_error(&project, "orchestrator", &format!("chat failed — {e}"));
        return Err(e);
    }
    workspace::touch_project(&project);
    workspace::append_chatlog(&project, "user", &message).map_err(|e| {
        log_project_error(&project, "orchestrator", &format!("chat log write failed — {e}"));
        err(e)
    })?;
    let workspace = project.to_string_lossy().to_string();
    let result = rpc(
        app,
        state.sidecar.clone(),
        "chat",
        json!({"workspace": workspace, "message": message}),
    )
    .await;
    if let Err(ref e) = result {
        log_project_error(&project, "orchestrator", &format!("chat failed — {e}"));
        return result;
    }
    if let Some(reply) = result.as_ref().ok().and_then(|r| r.get("reply").and_then(|v| v.as_str())) {
        workspace::append_chatlog(&project, "assistant", reply).map_err(|e| {
            log_project_error(&project, "orchestrator", &format!("chat log write failed — {e}"));
            err(e)
        })?;
    }
    result
}

#[allow(clippy::too_many_arguments)]
#[tauri::command]
async fn start_run(
    app: AppHandle,
    state: State<'_, AppState>,
    id: String,
    run_id: String,
    modules: Vec<String>,
    force: bool,
    force_reindex: bool,
    resume: bool,
    specific_instructions: Option<String>,
) -> CmdResult<Value> {
    let project = project_dir(&app, &id)?;
    if let Err(e) = ensure_configured(&app, &state, Some(&id)).await {
        log_project_error(&project, "run", &format!("run failed — {e}"));
        return Err(e);
    }
    let guard = state
        .jobs
        .try_begin(&app, &project, JobKind::Run, &id, "Running pipeline")
        .map_err(|busy| reject_job_busy(&app, &id, "run", busy))?;
    let workspace = project_workspace(&app, &id)?;
    let install = install_path(&app)?;
    let meta = workspace::read_meta(&workspace::project_dir(&install, &id)).map_err(|e| {
        log_project_error(&project, "run", &format!("run failed — {e}"));
        err(e)
    })?;
    let result = rpc(
        app,
        state.sidecar.clone(),
        "run_modules",
        json!({
            "workspace": workspace,
            "runId": run_id,
            "modules": modules,
            "projectType": meta.project_type,
            "force": force,
            "forceReindex": force_reindex,
            "resume": resume,
            "specific_instructions": specific_instructions,
            "appVersion": APP_VERSION,
        }),
    )
    .await;
    match &result {
        Err(e) => {
            let _ = workspace::set_run_status(&install, &id, &run_id, "cancelled");
            guard.fail_with("run", &format!("run_modules RPC failed — {e}"));
        }
        Ok(v) => match v.get("status").and_then(|s| s.as_str()) {
            Some("failed") | Some("cancelled") => {
                let status = v.get("status").and_then(|s| s.as_str()).unwrap_or("failed");
                let _ = workspace::set_run_status(&install, &id, &run_id, status);
                let err = v
                    .get("error")
                    .and_then(|e| e.as_str())
                    .or_else(|| v.get("failedModule").and_then(|m| m.as_str()))
                    .unwrap_or("run failed");
                guard.fail_with("run", &format!("run_modules {status} — {err}"));
            }
            _ => {}
        },
    }
    drop(guard);
    result
}

#[tauri::command]
fn stop_run(state: State<'_, AppState>) -> CmdResult<()> {
    state.sidecar.cancel();
    Ok(())
}

#[tauri::command]
async fn preview_file(app: AppHandle, state: State<'_, AppState>, id: String, path: String, base: Option<String>) -> CmdResult<Value> {
    let workspace = project_workspace(&app, &id)?;
    rpc(app, state.sidecar.clone(), "preview",
        json!({"workspace": workspace, "path": path, "base": base.unwrap_or_else(|| "repos".into())})).await
}

/// Generate a runId: timestamp + short random (spec 9.1).
#[tauri::command]
fn new_run_id() -> String {
    use rand::Rng;
    let ts = chrono::Local::now().format("%Y%m%d-%H%M%S");
    let chars: Vec<char> = "0123456789abcdef".chars().collect();
    let mut rng = rand::thread_rng();
    let suffix: String = (0..4).map(|_| chars[rng.gen_range(0..chars.len())]).collect();
    format!("{ts}-{suffix}")
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let sidecar_dir = sidecar_dir();
    let state = AppState {
        sidecar: Arc::new(SidecarManager::new(sidecar_dir)),
        jobs: Arc::new(JobManager::new()),
    };

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .manage(state)
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(state) = window.app_handle().try_state::<AppState>() {
                    state.sidecar.stop();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            check_prereqs,
            install_prereqs,
            get_setup_status,
            default_install_folder,
            pick_install_folder,
            set_install_path,
            setup_template,
            set_api_key,
            clear_api_key,
            stored_api_key_masked,
            list_git_credential_hosts,
            set_git_credential,
            clear_git_credential,
            github_cli_status,
            setup_github_cli_git,
            set_supabase_token,
            clear_supabase_token,
            stored_supabase_token_masked,
            list_supabase_projects,
            add_supabase,
            remove_supabase,
            list_supabase,
            snapshot_supabase,
            add_audio_files,
            add_audio_paths,
            list_audio,
            transcribe_audio,
            get_settings,
            set_models,
            list_models,
            suggest_project_id,
            list_projects,
            create_project,
            rename_project,
            delete_project,
            set_active_project,
            set_project_type,
            add_repo,
            remove_repo,
            clone_repos,
            list_repos,
            pull_repos,
            upload_meta_files,
            create_meta_dir,
            rename_meta_entry,
            move_meta_entry,
            list_meta_dir,
            list_meta_files,
            delete_meta_entry,
            delete_meta_file,
            get_chatlog,
            list_modules,
            get_module_suggestions,
            get_module_selection,
            set_module_selection,
            get_event_log,
            get_run_log,
            get_run_state,
            list_runs,
            get_run_artifacts,
            get_run_activity,
            configure_sidecar,
            sidecar_health,
            send_chat,
            start_run,
            stop_run,
            new_run_id,
            pull_project_modules,
            get_modules_version_status,
            preview_file,
            get_index,
            read_index_item,
            set_index_annotation,
            list_jobs,
            stop_job
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

/// In dev, the sidecar lives next to the app at ../sidecar; in release the frozen
/// binary sits next to the exe (handled inside SidecarManager).
fn sidecar_dir() -> PathBuf {
    if cfg!(debug_assertions) {
        // CARGO_MANIFEST_DIR = app/src-tauri ; sidecar is app/sidecar
        let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        manifest.parent().map(|p| p.join("sidecar")).unwrap_or_else(|| PathBuf::from("sidecar"))
    } else {
        std::env::current_exe()
            .ok()
            .and_then(|e| e.parent().map(PathBuf::from))
            .unwrap_or_else(|| PathBuf::from("."))
    }
}
