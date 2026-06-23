/** Mirror of Rust https_repo_needs_pat / missing_pat_host for Repositories UI. */

export function gitHostFromUrl(url: string): string | null {
  try {
    return new URL(url.trim()).hostname.toLowerCase();
  } catch {
    return null;
  }
}

export function repoNeedsHttpsPat(url: string): boolean {
  const t = url.trim();
  if (!t.startsWith("https://") && !t.startsWith("http://")) return false;
  if (t.includes("@")) return true;
  const host = gitHostFromUrl(t);
  return host === "dev.azure.com" || (host?.endsWith(".visualstudio.com") ?? false);
}

export function missingPatHosts(
  repos: { url: string }[],
  configuredHosts: Iterable<string>,
): string[] {
  const configured = new Set([...configuredHosts].map((h) => h.toLowerCase()));
  const missing = new Set<string>();
  for (const r of repos) {
    if (!repoNeedsHttpsPat(r.url)) continue;
    const host = gitHostFromUrl(r.url);
    if (host && !configured.has(host)) missing.add(host);
  }
  return [...missing].sort();
}
