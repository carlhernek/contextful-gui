//! Per-host git HTTPS tokens stored in the OS keychain (PAT for Azure DevOps, GitHub, etc.).

use anyhow::{Context, Result};
use keyring::Entry;

const SERVICE: &str = "contextful";

fn entry(host: &str) -> Result<Entry> {
    let host = normalize_host(host);
    Entry::new(SERVICE, &format!("git:{host}")).context("open git credential keychain entry")
}

pub fn normalize_host(host: &str) -> String {
    host.trim().to_lowercase()
}

pub fn save(host: &str, token: &str) -> Result<()> {
    let token = token.trim();
    if token.is_empty() {
        anyhow::bail!("token is empty");
    }
    entry(host)?
        .set_password(token)
        .context("store git credential")
}

pub fn load(host: &str) -> Result<Option<String>> {
    match entry(host)?.get_password() {
        Ok(token) => Ok(Some(token)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(e).context("read git credential"),
    }
}

pub fn delete(host: &str) -> Result<()> {
    match entry(host)?.delete_credential() {
        Ok(()) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(e).context("delete git credential"),
    }
}

pub fn masked(host: &str) -> Result<Option<String>> {
    let Some(token) = load(host)? else {
        return Ok(None);
    };
    if token.len() <= 8 {
        return Ok(Some("••••••••".to_string()));
    }
    Ok(Some(format!("{}...{}", &token[..4], &token[token.len() - 4..])))
}

#[cfg(test)]
mod tests {
    use super::normalize_host;

    #[test]
    fn normalize_host_lowercases() {
        assert_eq!(normalize_host("Dev.Azure.com"), "dev.azure.com");
    }
}
