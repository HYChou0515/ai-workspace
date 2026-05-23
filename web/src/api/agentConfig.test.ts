import { beforeEach, describe, expect, it } from "vitest";

import { mockApi } from "./mock";

describe("agent config picker (mock client)", () => {
  let inv: string;

  beforeEach(async () => {
    const created = await mockApi.createInvestigation({ title: "pick test" });
    inv = created.resource_id;
  });

  it("lists at least one local + one hosted config", async () => {
    const configs = await mockApi.listAgentConfigs();
    const models = configs.map((c) => c.model);
    expect(models.some((m) => m.includes("qwen"))).toBe(true);
    expect(models.some((m) => m.includes("claude"))).toBe(true);
    // every entry is renderable as a radio option
    for (const c of configs) {
      expect(c.resource_id).toBeTruthy();
      expect(c.name).toBeTruthy();
    }
  });

  it("attaching a config sticks to the investigation", async () => {
    const configs = await mockApi.listAgentConfigs();
    const target = configs.find((c) => c.model.includes("claude"))!;
    await mockApi.attachAgentConfig(inv, target.resource_id);
    const got = await mockApi.getInvestigation(inv);
    expect(got.attached_agent_config_id).toBe(target.resource_id);
  });

  it("detaching with null clears the attachment", async () => {
    const configs = await mockApi.listAgentConfigs();
    await mockApi.attachAgentConfig(inv, configs[0]!.resource_id);
    await mockApi.attachAgentConfig(inv, null);
    const got = await mockApi.getInvestigation(inv);
    expect(got.attached_agent_config_id).toBeNull();
  });
});

describe("close investigation (mock client)", () => {
  it("resolve/abandon flip the status", async () => {
    const created = await mockApi.createInvestigation({ title: "to resolve" });
    await mockApi.closeInvestigation(created.resource_id, "resolved");
    expect((await mockApi.getInvestigation(created.resource_id)).status).toBe("resolved");
  });

  it("pure close (null) leaves status untouched", async () => {
    const created = await mockApi.createInvestigation({ title: "still open" });
    await mockApi.closeInvestigation(created.resource_id, null);
    expect((await mockApi.getInvestigation(created.resource_id)).status).toBe("triaging");
  });
});
