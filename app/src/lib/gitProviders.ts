/** Git provider definitions + URL host classification for the Connections tab. */

export type GitProviderId = "github" | "azure" | "custom";

export interface GitProvider {
  id: GitProviderId;
  label: string;
  /** Fixed credential host, or null when the user supplies it (custom). */
  credentialHost: string | null;
  needsUsername: boolean;
  /** Page where the user creates a PAT for this provider. */
  tokenUrl?: string;
  urlPlaceholder: string;
}

export const GIT_PROVIDERS: GitProvider[] = [
  {
    id: "github",
    label: "GitHub",
    credentialHost: "github.com",
    needsUsername: false,
    tokenUrl: "https://github.com/settings/tokens",
    urlPlaceholder: "https://github.com/org/repo.git",
  },
  {
    id: "azure",
    label: "Azure DevOps",
    credentialHost: "dev.azure.com",
    needsUsername: true,
    urlPlaceholder: "https://dev.azure.com/org/project/_git/repo",
  },
  {
    id: "custom",
    label: "Custom / Other",
    credentialHost: null,
    needsUsername: false,
    urlPlaceholder: "https://git.example.com/org/repo.git",
  },
];

export function getProvider(id: GitProviderId): GitProvider {
  return GIT_PROVIDERS.find((p) => p.id === id) ?? GIT_PROVIDERS[GIT_PROVIDERS.length - 1];
}

/** Parse the host from an https:// URL or an scp-style git@host:path SSH URL. */
export function hostFromGitUrl(url: string): string | null {
  const t = url.trim();
  if (!t) return null;
  const scp = /^[^/@]+@([^:/]+):/.exec(t);
  if (scp) return scp[1].toLowerCase();
  try {
    return new URL(t).hostname.toLowerCase();
  } catch {
    return null;
  }
}

/** Classify a repo URL into a provider tab. */
export function providerForUrl(url: string): GitProviderId {
  const host = hostFromGitUrl(url);
  if (!host) return "custom";
  if (host === "github.com") return "github";
  if (host === "dev.azure.com" || host.endsWith(".visualstudio.com")) return "azure";
  return "custom";
}
