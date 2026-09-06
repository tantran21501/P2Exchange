export interface Env {
  GITHUB_ACTIONS_DISPATCH_TOKEN: string;
  GITHUB_OWNER: string;
  GITHUB_REPO: string;
  GITHUB_REF: string;
  PAIR_WORKFLOW: string;
}

export const PAIR_CRON = "27 * * * *";
const GITHUB_API_VERSION = "2026-03-10";

function required(value: string | undefined, name: keyof Env): string {
  if (!value) throw new Error(`Missing required Worker binding: ${name}`);
  return value;
}

export function workflowDispatchUrl(env: Env): string {
  const owner = encodeURIComponent(required(env.GITHUB_OWNER, "GITHUB_OWNER"));
  const repo = encodeURIComponent(required(env.GITHUB_REPO, "GITHUB_REPO"));
  const workflow = encodeURIComponent(required(env.PAIR_WORKFLOW, "PAIR_WORKFLOW"));
  return `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`;
}

export async function dispatchPairWorkflow(env: Env, fetcher: typeof fetch = fetch): Promise<void> {
  const response = await fetcher(workflowDispatchUrl(env), {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${required(env.GITHUB_ACTIONS_DISPATCH_TOKEN, "GITHUB_ACTIONS_DISPATCH_TOKEN")}`,
      "Content-Type": "application/json",
      "User-Agent": "P2Exchange-POE2ScoutPairScheduler/1.0 (cloudflare-worker)",
      "X-GitHub-Api-Version": GITHUB_API_VERSION,
    },
    body: JSON.stringify({ ref: required(env.GITHUB_REF, "GITHUB_REF"), inputs: { league: "Forbidden Rites" } }),
  });
  if (!response.ok) {
    const detail = (await response.text()).trim() || response.statusText;
    throw new Error(`GitHub pair workflow dispatch failed: ${response.status} ${detail}`);
  }
  console.log("Dispatched compact POE2 Scout pair workflow.");
}

export default {
  async scheduled(controller: ScheduledController, env: Env): Promise<void> {
    if (controller.cron !== PAIR_CRON) throw new Error(`Unexpected cron: ${controller.cron}`);
    await dispatchPairWorkflow(env);
  },
};
