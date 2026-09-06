import { describe, expect, it, vi } from "vitest";
import { dispatchPairWorkflow, workflowDispatchUrl, type Env } from "../src/index";

function env(): Env {
  return {
    GITHUB_ACTIONS_DISPATCH_TOKEN: "github_pat_test",
    GITHUB_OWNER: "tantran21501",
    GITHUB_REPO: "P2Exchange",
    GITHUB_REF: "main",
    PAIR_WORKFLOW: "poe2scout-pair-snapshot.yml",
  };
}

describe("pair workflow scheduler", () => {
  it("dispatches only the configured workflow without logging the token", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    const log = vi.spyOn(console, "log").mockImplementation(() => undefined);
    await dispatchPairWorkflow(env(), fetcher);
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(workflowDispatchUrl(env()));
    expect(init.body).toBe(JSON.stringify({ ref: "main", inputs: { league: "Forbidden Rites" } }));
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer github_pat_test");
    expect(log.mock.calls.flat().join(" ")).not.toContain("github_pat_test");
    log.mockRestore();
  });

  it("fails clearly when GitHub rejects the dispatch", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("bad credentials", { status: 401, statusText: "Unauthorized" }),
    );
    await expect(dispatchPairWorkflow(env(), fetcher)).rejects.toThrow(
      "GitHub pair workflow dispatch failed: 401 bad credentials",
    );
  });
});
