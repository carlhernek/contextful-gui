//! OS-keychain storage for the OpenRouter API key (spec section 10.1).

use anyhow::{Context, Result};
use keyring::Entry;

const SERVICE: &str = "contextful";
const USER: &str = "openrouter-api-key";
const SUPABASE_PAT_USER: &str = "supabase-management-pat";

fn entry() -> Result<Entry> {
    Entry::new(SERVICE, USER).context("open keychain entry")
}

fn supabase_entry() -> Result<Entry> {
    Entry::new(SERVICE, SUPABASE_PAT_USER).context("open supabase pat keychain entry")
}

pub fn save_api_key(key: &str) -> Result<()> {
    entry()?.set_password(key).context("store api key")
}

pub fn load_api_key() -> Result<Option<String>> {
    match entry()?.get_password() {
        Ok(k) => Ok(Some(k)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(e).context("read api key"),
    }
}

pub fn delete_api_key() -> Result<()> {
    match entry()?.delete_credential() {
        Ok(()) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(e).context("delete api key"),
    }
}

/// First 7 chars + "..." + last 4, or None if no key is stored.
pub fn masked_api_key() -> Result<Option<String>> {
    let Some(key) = load_api_key()? else {
        return Ok(None);
    };
    mask(&key)
}

// --- Supabase Management API personal access token (account-level) --------
pub fn save_supabase_pat(pat: &str) -> Result<()> {
    let pat = pat.trim();
    if pat.is_empty() {
        anyhow::bail!("token is empty");
    }
    supabase_entry()?.set_password(pat).context("store supabase pat")
}

pub fn load_supabase_pat() -> Result<Option<String>> {
    match supabase_entry()?.get_password() {
        Ok(k) => Ok(Some(k)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(e).context("read supabase pat"),
    }
}

pub fn delete_supabase_pat() -> Result<()> {
    match supabase_entry()?.delete_credential() {
        Ok(()) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(e).context("delete supabase pat"),
    }
}

pub fn masked_supabase_pat() -> Result<Option<String>> {
    let Some(pat) = load_supabase_pat()? else {
        return Ok(None);
    };
    mask(&pat)
}

fn mask(key: &str) -> Result<Option<String>> {
    if key.len() <= 11 {
        return Ok(Some("•".repeat(key.len().max(3))));
    }
    let head = &key[..7];
    let tail = &key[key.len() - 4..];
    Ok(Some(format!("{head}...{tail}")))
}
