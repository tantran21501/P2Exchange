export interface Env {
  GITHUB_ACTIONS_DISPATCH_TOKEN: string;
  GITHUB_OWNER: string;
  GITHUB_REPO: string;
  GITHUB_REF: string;
  REWARD_WORKFLOW: string;
  FULL_CATEGORY_WORKFLOW: string;
}

export interface DispatchResult {
  workflow_run_id?: number;
  run_url?: string;
  html_url?: string;
}

export const REWARD_CRON = "7 * * * *";
export const FULL_CATEGORY_CRON = "17 * * * *";

const GITHUB_API_VERSION = "2026-03-10";

function required(value: string | undefined, name: keyof Env): string {
  if (!value) {
    throw new Error(`Missing required Worker binding: ${name}`);
  }
  return value;
}

export function workflowForCron(cron: string, env: Env): string {
  if (cron === REWARD_CRON) {
    return required(env.REWARD_WORKFLOW, "REWARD_WORKFLOW");
  }

  if (cron === FULL_CATEGORY_CRON) {
    return required(env.FULL_CATEGORY_WORKFLOW, "FULL_CATEGORY_WORKFLOW");
  }

  throw new Error(`No workflow configured for cron: ${cron}`);
}

export function workflowDispatchUrl(env: Env, workflowId: string): string {
  const owner = encodeURIComponent(required(env.GITHUB_OWNER, "GITHUB_OWNER"));
  const repo = encodeURIComponent(required(env.GITHUB_REPO, "GITHUB_REPO"));
  const workflow = encodeURIComponent(workflowId);

  return `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`;
}

export async function dispatchWorkflow(
  workflowId: string,
  env: Env,
  fetcher: typeof fetch = fetch,
): Promise<DispatchResult | null> {
  const response = await fetcher(workflowDispatchUrl(env, workflowId), {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${required(env.GITHUB_ACTIONS_DISPATCH_TOKEN, "GITHUB_ACTIONS_DISPATCH_TOKEN")}`,
      "Content-Type": "application/json",
      "User-Agent": "P2Exchange-POENinjaScheduler/1.0 (cloudflare-worker)",
      "X-GitHub-Api-Version": GITHUB_API_VERSION,
    },
    body: JSON.stringify({
      ref: required(env.GITHUB_REF, "GITHUB_REF"),
      inputs: { league: "Forbidden Rites" },
      return_run_details: true,
    }),
  });

  const text = await response.text();

  if (!response.ok) {
    const detail = text.trim() || response.statusText;
    throw new Error(`GitHub workflow dispatch failed for ${workflowId}: ${response.status} ${detail}`);
  }

  if (!text.trim()) {
    console.log(`Dispatched ${workflowId}; GitHub returned no run details.`);
    return null;
  }

  const result = JSON.parse(text) as DispatchResult;
  console.log(`Dispatched ${workflowId}: ${result.html_url ?? result.run_url ?? "run details unavailable"}`);
  return result;
}

export async function runScheduled(cron: string, env: Env): Promise<DispatchResult | null> {
  const workflowId = workflowForCron(cron, env);
  return dispatchWorkflow(workflowId, env);
}

export default {
  async scheduled(controller: ScheduledController, env: Env): Promise<void> {
    await runScheduled(controller.cron, env);
  },
};
