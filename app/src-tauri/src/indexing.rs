//! Workspace index annotations and index file access.

use std::fs;
use std::path::Path;

use anyhow::{Context, Result};
use chrono::Local;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

pub const INDEX_FILE: &str = ".workspace-index.json";
#[allow(dead_code)]
pub const ANNOTATIONS_FILE: &str = ".index-annotations.json";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IndexAnnotation {
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub keywords: Vec<String>,
    #[serde(default)]
    pub updated_at: Option<String>,
}

fn empty_index() -> Value {
    json!({
        "version": 1,
        "updatedAt": null,
        "project": {},
        "items": []
    })
}

/// Patch description/keywords on an existing item in `.workspace-index.json` (no sidecar, no LLM).
pub fn set_annotation(
    project: &Path,
    item_id: &str,
    description: &str,
    keywords: &[String],
) -> Result<IndexAnnotation> {
    let path = project.join(INDEX_FILE);
    if !path.exists() {
        anyhow::bail!(
            "index not built yet for {item_id} — run the Workspace Index module first"
        );
    }
    let raw = fs::read_to_string(&path).with_context(|| format!("read {INDEX_FILE}"))?;
    let mut index: Value =
        serde_json::from_str(&raw).with_context(|| format!("parse {INDEX_FILE}"))?;
    let items = index
        .get_mut("items")
        .and_then(|v| v.as_array_mut())
        .context("index items array missing")?;
    let pos = items
        .iter()
        .position(|it| it.get("id").and_then(|v| v.as_str()) == Some(item_id))
        .with_context(|| format!("index item not found: {item_id}"))?;
    let now = Local::now().to_rfc3339();
    let item = &mut items[pos];
    if let Some(obj) = item.as_object_mut() {
        obj.insert("description".into(), json!(description));
        obj.insert("keywords".into(), json!(keywords));
        obj.insert("source".into(), json!("user"));
        obj.insert("userEdited".into(), json!(true));
        obj.insert("enrichedAt".into(), json!(now));
    }
    if let Some(root) = index.as_object_mut() {
        root.insert("updatedAt".into(), json!(now));
    }
    fs::write(&path, serde_json::to_string_pretty(&index)?).context("write index")?;
    Ok(IndexAnnotation {
        description: description.to_string(),
        keywords: keywords.to_vec(),
        updated_at: Some(now),
    })
}

pub fn get_index(project: &Path) -> Result<Value> {
    let path = project.join(INDEX_FILE);
    if !path.exists() {
        return Ok(empty_index());
    }
    let raw = fs::read_to_string(&path).with_context(|| format!("read {INDEX_FILE}"))?;
    Ok(serde_json::from_str(&raw).unwrap_or_else(|_| empty_index()))
}

pub fn read_index_item(project: &Path, item_id: &str) -> Result<Option<Value>> {
    let index = get_index(project)?;
    let items = index
        .get("items")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    Ok(items.into_iter().find(|it| it.get("id").and_then(|v| v.as_str()) == Some(item_id)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::{write_meta, ProjectMeta};
    use std::path::PathBuf;
    use tempfile::tempdir;

    fn fixture_project() -> (tempfile::TempDir, PathBuf) {
        let tmp = tempdir().unwrap();
        let project = tmp.path().join("projects").join("demo");
        fs::create_dir_all(&project).unwrap();
        write_meta(
            &project,
            &ProjectMeta {
                display_name: "Demo".to_string(),
                project_type: "both".to_string(),
                repos: vec![],
                supabase: vec![],
                models: serde_json::Map::new(),
                selected_modules: vec![],
                deleted: false,
            },
        )
        .unwrap();
        (tmp, project)
    }

    #[test]
    fn set_annotation_patches_index_file() {
        let (_tmp, project) = fixture_project();
        fs::write(
            project.join(INDEX_FILE),
            r#"{"version":1,"items":[{"id":"repo:backoffice","type":"repo","description":"old","keywords":[]}]}"#,
        )
        .unwrap();
        let ann = set_annotation(
            &project,
            "repo:backoffice",
            "Admin API service",
            &["backoffice".into(), "orders".into()],
        )
        .unwrap();
        assert_eq!(ann.description, "Admin API service");
        assert_eq!(ann.keywords.len(), 2);

        let index = get_index(&project).unwrap();
        let item = index["items"]
            .as_array()
            .unwrap()
            .iter()
            .find(|it| it["id"] == "repo:backoffice")
            .unwrap();
        assert_eq!(item["description"], "Admin API service");
        assert_eq!(item["source"], "user");
        assert!(item["userEdited"].as_bool().unwrap());
    }

    #[test]
    fn set_annotation_requires_existing_index_item() {
        let (_tmp, project) = fixture_project();
        fs::write(
            project.join(INDEX_FILE),
            r#"{"version":1,"items":[{"id":"meta:req.md","type":"meta"}]}"#,
        )
        .unwrap();
        assert!(set_annotation(&project, "repo:missing", "x", &[]).is_err());
    }

    #[test]
    fn get_index_missing_returns_empty() {
        let (_tmp, project) = fixture_project();
        let index = get_index(&project).unwrap();
        assert_eq!(index["items"].as_array().unwrap().len(), 0);
    }

    #[test]
    fn legacy_project_index_lazy_compat() {
        let tmp = tempdir().unwrap();
        let project = tmp.path().join("projects/legacy-1-0-0");
        fs::create_dir_all(project.join("meta")).unwrap();
        write_meta(
            &project,
            &ProjectMeta {
                display_name: "Legacy 1.0.0".to_string(),
                project_type: "both".to_string(),
                repos: vec![crate::workspace::RepoEntry {
                    name: "backoffice".to_string(),
                    url: String::new(),
                    branch: "develop".to_string(),
                }],
                supabase: vec![],
                models: serde_json::Map::new(),
                selected_modules: vec![],
                deleted: false,
            },
        )
        .unwrap();
        fs::write(project.join("meta/requirements.md"), "# req").unwrap();
        fs::create_dir_all(project.join("modules")).unwrap();
        fs::write(project.join("modules/template-version.txt"), "1.0.0").unwrap();
        fs::create_dir_all(project.join("runs/old/m")).unwrap();
        fs::write(project.join("runs/old/m/analysis.md"), "ok").unwrap();

        assert!(!project.join(INDEX_FILE).exists());
        assert!(!project.join(ANNOTATIONS_FILE).exists());

        let index = get_index(&project).unwrap();
        assert_eq!(index["items"].as_array().unwrap().len(), 0);

        fs::write(
            project.join(INDEX_FILE),
            r#"{"version":1,"items":[{"id":"meta:requirements.md","type":"meta","description":"","keywords":[]}]}"#,
        )
        .unwrap();
        set_annotation(&project, "meta:requirements.md", "User note", &["req".into()]).unwrap();
        assert!(!project.join(ANNOTATIONS_FILE).exists());

        let meta_before = fs::read_to_string(project.join("meta/requirements.md")).unwrap();
        crate::workspace::touch_project(&project);
        assert_eq!(
            fs::read_to_string(project.join("meta/requirements.md")).unwrap(),
            meta_before
        );
    }

    #[test]
    fn set_annotation_updates_existing_item_in_place() {
        let (_tmp, project) = fixture_project();
        fs::write(
            project.join(INDEX_FILE),
            r#"{"version":1,"items":[{"id":"meta:req.md","description":"auto","keywords":[]}]}"#,
        )
        .unwrap();
        set_annotation(&project, "meta:req.md", "Requirements doc", &["req".into()]).unwrap();
        let index = get_index(&project).unwrap();
        let item = &index["items"][0];
        assert_eq!(item["description"], "Requirements doc");
        assert_eq!(item["source"], "user");
    }
}
