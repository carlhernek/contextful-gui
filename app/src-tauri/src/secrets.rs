//! OS-keychain storage for the OpenRouter API key (spec section 10.1).

use anyhow::{Context, Result};
use keyring::Entry;

const SERVICE: &str = "contextful";
const USER: &str = "openrouter-api-key";

fn entry() -> Result<Entry> {
    Entry::new(SERVICE, USER).context("open keychain entry")
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
    if key.len() <= 11 {
        return Ok(Some("•".repeat(key.len().max(3))));
    }
    let head = &key[..7];
    let tail = &key[key.len() - 4..];
    Ok(Some(format!("{head}...{tail}")))
}
