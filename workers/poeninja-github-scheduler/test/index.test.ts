import { describe, expect, it, vi } from "vitest";

import {
  dispatchWorkflow,
  FULL_CATEGORY_CRON,
  REWARD_CRON,
  runScheduled,
  workflowDispatchUrl,
  workflowForCron,
  type Env,
} from "../src/index";

function env(overrides: Partial<Env> = {}): Env {
  return {
    GITHUB_ACTIONS_DISPATCH_TOKEN: "ghp_test_token",
    GITHUB_OWNER: "tantran21501",
    GITHUB_REPO: "P2Exchange",
    GITHUB_REF: "main",
    REWARD_WORKFLOW: "poeninja-reward-price-snapshot.yml",
    FULL_CATEGORY_WORKFLOW: "poeninja-full-category-snapshot.yml",
    ...overrides,
  };
}

function response(body: string, init: ResponseInit = {}): Response {
  return new Response(body, {
    status: 200,
    ...init,
  });
}

describe("workflowForCron", () => {
  it("maps the reward cron to the reward workflow", () => {
    expect(workflowForCron(REWARD_CRON, env())).toBe("poeninja-reward-price-snapshot.yml");
  });

  it("maps the full category cron to the full category workflow", () => {
    expect(workflowForCron(FULL_CATEGORY_CRON, env())).toBe("poeninja-full-category-snapshot.yml");
  });

  it("fails clearly for unknown cron schedules", () => {
    expect(() => workflowForCron("0 * * * *", env())).toThrow("No workflow configured");
  });
});

describe("dispatchWorkflow", () => {
  it("calls the GitHub workflow dispatch endpoint with the expected body and headers", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      response(JSON.stringify({
        workflow_run_id: 123,
        run_url: "https://api.github.com/repos/tantran21501/P2Exchange/actions/runs/123",
        html_url: "https://github.com/tantran21501/P2Exchange/actions/runs/123",
      })),
    );

    const result = await dispatchWorkflow("poeninja-reward-price-snapshot.yml", env(), fetcher);

    expect(result?.workflow_run_id).toBe(123);
    expect(fetcher).toHaveBeenCalledTimes(1);

    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "https://api.github.com/repos/tantran21501/P2Exchange/actions/workflows/poeninja-reward-price-snapshot.yml/dispatches",
    );
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({
      ref: "main",
      inputs: {},
      return_run_details: true,
    }));

    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer ghp_test_token");
    expect(headers.Accept).toBe("application/vnd.github+json");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(headers["X-GitHub-Api-Version"]).toBe("2026-03-10");
  });

  it("does not log the token when dispatch succeeds", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response("{}"));
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => undefined);

    await dispatchWorkflow("poeninja-reward-price-snapshot.yml", env(), fetcher);

    expect(logSpy).toHaveBeenCalledTimes(1);
    expect(logSpy.mock.calls.flat().join(" ")).not.toContain("ghp_test_token");
    logSpy.mockRestore();
  });

  it("throws a clear error when GitHub returns a non-2xx response", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response("bad credentials", {
      status: 401,
      statusText: "Unauthorized",
    }));

    await expect(
      dispatchWorkflow("poeninja-reward-price-snapshot.yml", env(), fetcher),
    ).rejects.toThrow(
      "GitHub workflow dispatch failed for poeninja-reward-price-snapshot.yml: 401 bad credentials",
    );
  });
});

describe("runScheduled", () => {
  it("dispatches the reward workflow at minute 7", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response("{}"));
    vi.stubGlobal("fetch", fetcher);

    await runScheduled(REWARD_CRON, env());

    expect(fetcher.mock.calls[0]?.[0]).toBe(
      workflowDispatchUrl(env(), "poeninja-reward-price-snapshot.yml"),
    );
    vi.unstubAllGlobals();
  });

  it("dispatches the full category workflow at minute 17", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response("{}"));
    vi.stubGlobal("fetch", fetcher);

    await runScheduled(FULL_CATEGORY_CRON, env());

    expect(fetcher.mock.calls[0]?.[0]).toBe(
      workflowDispatchUrl(env(), "poeninja-full-category-snapshot.yml"),
    );
    vi.unstubAllGlobals();
  });
});
