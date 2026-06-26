//! Central job registry: single-flight long-running work with app-wide busy state.

use std::cell::Cell;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use chrono::Local;
use serde::Serialize;
use tauri::{AppHandle, Emitter, Runtime};

use crate::sidecar::{SidecarEvent, EVENT_NAME};
use crate::workspace;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum JobKind {
    Run,
    Index,
    Clone,
    Pull,
    Snapshot,
    Transcribe,
}

impl JobKind {
    pub fn as_str(self) -> &'static str {
        match self {
            JobKind::Run => "run",
            JobKind::Index => "index",
            JobKind::Clone => "clone",
            JobKind::Pull => "pull",
            JobKind::Snapshot => "snapshot",
            JobKind::Transcribe => "transcribe",
        }
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Job {
    pub key: String,
    pub kind: JobKind,
    pub project_id: String,
    pub label: String,
    pub started_at: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct JobBusy {
    pub current: Job,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct JobEvent {
    action: String,
    job: Job,
}

pub struct JobManager {
    current: Mutex<Option<Job>>,
    cancelled: AtomicBool,
    cancel_logged: AtomicBool,
}

impl JobManager {
    pub fn new() -> Self {
        Self {
            current: Mutex::new(None),
            cancelled: AtomicBool::new(false),
            cancel_logged: AtomicBool::new(false),
        }
    }

    pub fn snapshot(&self) -> Vec<Job> {
        self.current
            .lock()
            .unwrap()
            .clone()
            .map(|j| vec![j])
            .unwrap_or_default()
    }

    pub fn current(&self) -> Option<Job> {
        self.current.lock().unwrap().clone()
    }

    pub fn mark_cancelled(&self) {
        self.cancelled.store(true, Ordering::SeqCst);
    }

    pub fn mark_cancelled_logged(&self) {
        self.cancelled.store(true, Ordering::SeqCst);
        self.cancel_logged.store(true, Ordering::SeqCst);
    }

    /// Begin a job or reject if another is active (user-initiated operations).
    pub fn try_begin<R: Runtime>(
        self: &Arc<Self>,
        app: &AppHandle<R>,
        project: &Path,
        kind: JobKind,
        project_id: &str,
        label: &str,
    ) -> Result<JobGuard<R>, JobBusy> {
        self.try_begin_inner(app, project, kind, project_id, label, false)
    }

    /// Begin a job or return None if another is active (auto/coalesced operations).
    pub fn try_begin_or_skip<R: Runtime>(
        self: &Arc<Self>,
        app: &AppHandle<R>,
        project: &Path,
        kind: JobKind,
        project_id: &str,
        label: &str,
    ) -> Option<JobGuard<R>> {
        match self.try_begin_inner(app, project, kind, project_id, label, true) {
            Ok(guard) => Some(guard),
            Err(_) => None,
        }
    }

    fn try_begin_inner<R: Runtime>(
        self: &Arc<Self>,
        app: &AppHandle<R>,
        project: &Path,
        kind: JobKind,
        project_id: &str,
        label: &str,
        skip_if_busy: bool,
    ) -> Result<JobGuard<R>, JobBusy> {
        self.cancelled.store(false, Ordering::SeqCst);
        self.cancel_logged.store(false, Ordering::SeqCst);
        let mut slot = self.current.lock().unwrap();
        if let Some(current) = slot.clone() {
            if skip_if_busy {
                return Err(JobBusy { current }); // caller treats as skip via try_begin_or_skip
            }
            return Err(JobBusy { current });
        }

        let job = Job {
            key: format!("{}:{}", kind.as_str(), project_id),
            kind,
            project_id: project_id.to_string(),
            label: label.to_string(),
            started_at: Local::now().to_rfc3339(),
        };
        *slot = Some(job.clone());
        drop(slot);

        workspace::append_eventlog(
            project,
            "job",
            "START",
            &format!("{label} ({})", kind.as_str()),
        );
        emit_job_event(app, "started", &job);
        Ok(JobGuard {
            manager: Arc::clone(self),
            app: app.clone(),
            project: project.to_path_buf(),
            job: Some(job),
            failed: Cell::new(false),
        })
    }

    fn finish<R: Runtime>(&self, app: &AppHandle<R>, project: &Path, job: &Job, failed: bool) {
        let cancelled = self.cancelled.swap(false, Ordering::SeqCst);
        let already_logged = self.cancel_logged.swap(false, Ordering::SeqCst);
        let (status, detail) = if cancelled {
            ("CANCELLED", "stopped by user")
        } else if failed {
            ("ERROR", "failed")
        } else {
            ("SUCCESS", "finished")
        };
        if !(cancelled && already_logged) {
            workspace::append_eventlog(
                project,
                "job",
                status,
                &format!("{} ({}) — {detail}", job.label, job.kind.as_str()),
            );
        }
        emit_job_event(
            app,
            if cancelled {
                "cancelled"
            } else if failed {
                "failed"
            } else {
                "finished"
            },
            job,
        );
    }
}

pub struct JobGuard<R: Runtime> {
    manager: Arc<JobManager>,
    app: AppHandle<R>,
    project: PathBuf,
    job: Option<Job>,
    failed: Cell<bool>,
}

impl<R: Runtime> JobGuard<R> {
    pub fn fail(&self) {
        self.failed.set(true);
    }

    /// Mark the job failed and append a scope-specific ERROR line (in addition to job ERROR on drop).
    pub fn fail_with(&self, scope: &str, message: &str) {
        workspace::append_eventlog(&self.project, scope, "ERROR", message);
        self.failed.set(true);
    }
}

impl<R: Runtime> Drop for JobGuard<R> {
    fn drop(&mut self) {
        if let Some(job) = self.job.take() {
            self.manager.current.lock().unwrap().take();
            self.manager.finish(
                &self.app,
                &self.project,
                &job,
                self.failed.get(),
            );
        }
    }
}

fn emit_job_event<R: Runtime>(app: &AppHandle<R>, action: &str, job: &Job) {
    let _ = app.emit(
        EVENT_NAME,
        SidecarEvent {
            id: None,
            event: "job".to_string(),
            data: serde_json::to_value(JobEvent {
                action: action.to_string(),
                job: job.clone(),
            })
            .unwrap_or_default(),
        },
    );
}

pub fn busy_error(busy: JobBusy) -> String {
    format!(
        "Contextful is busy with {} ({})",
        busy.current.label,
        busy.current.kind.as_str()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshot_empty_by_default() {
        let mgr = JobManager::new();
        assert!(mgr.snapshot().is_empty());
    }

    #[test]
    fn slot_blocks_while_held() {
        let mgr = Arc::new(JobManager::new());
        {
            let mut slot = mgr.current.lock().unwrap();
            *slot = Some(Job {
                key: "run:p1".into(),
                kind: JobKind::Run,
                project_id: "p1".into(),
                label: "Running".into(),
                started_at: "now".into(),
            });
        }
        assert!(mgr.current().is_some());
        assert_eq!(mgr.snapshot().len(), 1);
        {
            let mut slot = mgr.current.lock().unwrap();
            *slot = None;
        }
        assert!(mgr.snapshot().is_empty());
    }

    #[test]
    fn busy_error_message() {
        let msg = busy_error(JobBusy {
            current: Job {
                key: "run:p1".into(),
                kind: JobKind::Run,
                project_id: "p1".into(),
                label: "Running pipeline".into(),
                started_at: "now".into(),
            },
        });
        assert!(msg.contains("Running pipeline"));
        assert!(msg.contains("run"));
    }
}
