import type { Task } from "./ipc";

const REPO_EVIDENCE = /^repos\/([^/]+)\//;

export interface InferredRepo {
  name: string;
  count: number;
}

/** Extract repo folder names from evidence paths, sorted by citation count. */
export function inferReposFromEvidence(evidence: string[]): InferredRepo[] {
  const counts = new Map<string, number>();
  for (const path of evidence) {
    const match = path.match(REPO_EVIDENCE);
    if (match) {
      const name = match[1];
      counts.set(name, (counts.get(name) ?? 0) + 1);
    }
  }
  return [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

function formatRepoOpener(repos: InferredRepo[], evidence: string[]): string {
  if (repos.length === 1) {
    const { name } = repos[0];
    return `You are a coding agent working in the \`${name}\` repository (\`repos/${name}/\`).`;
  }
  if (repos.length > 1) {
    const listed = repos
      .map(({ name }) => `\`${name}\` (\`repos/${name}/\`)`)
      .join(", ");
    return `You are a coding agent working across these repositories: ${listed}. Primary: \`${repos[0].name}\`.`;
  }
  const hasMeta = evidence.some((p) => p.startsWith("meta/"));
  if (hasMeta) {
    return "You are a coding agent working from project meta documents (no single repo identified — inspect evidence paths below).";
  }
  return "You are a coding agent completing an analysis follow-up task (inspect evidence below for scope).";
}

/**
 * Build a self-contained prompt an engineer can paste into a coding agent.
 * Composes task fields even when agentic_spec alone is terse or step-list shaped.
 */
export function formatTaskPrompt(task: Task, moduleId?: string): string {
  const repos = inferReposFromEvidence(task.evidence);
  const lines: string[] = [formatRepoOpener(repos, task.evidence)];

  if (moduleId) {
    lines.push(`Context: follow-up from the "${moduleId}" analysis module (task ${task.id}).`);
  } else {
    lines.push(`Context: follow-up task ${task.id}.`);
  }

  lines.push("", `## Objective`, task.title, "", `## Background`, task.rationale.trim());

  if (task.evidence.length > 0) {
    lines.push("", "## Files to inspect (start here)");
    for (const path of task.evidence) {
      lines.push(`- ${path}`);
    }
  }

  const spec = task.agentic_spec?.trim();
  if (spec) {
    lines.push("", "## What to do", spec);
  }

  lines.push(
    "",
    "## Done when",
    `- ${task.title} is fully addressed.`,
    "- Changes stay scoped to this task — no unrelated refactors.",
    "- You verify the fix (run relevant tests or describe manual validation).",
  );

  return lines.join("\n").trimEnd() + "\n";
}
