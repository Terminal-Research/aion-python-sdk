import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it, expect } from "vitest";
import { resolveWorkspace, registryAccountScope, registryAgentKey, eligibleLegacyRegistryAgent, normalizeRegistryPreferences } from "../src/lib/workspaces.js";
import { loadChatSettings, saveChatSettings } from "../src/lib/chatSettings.js";
import { createAgentKey } from "../src/lib/agents/model.js";
import type { RegistryAccount } from "../src/lib/graphql/registry.js";

const home = { id: "home", name: "Personal", organizationType: "Personal" as const };
const team = { id: "team", name: "Team Space", organizationType: "Team" as const };
const account: RegistryAccount = { userId: "user", homeWorkspaceId: "home", workspaces: [home, team] };

describe("workspace resolution and preferences", () => {
	it("resolves explicit ID or exact unique name before saved and home choices", () => {
		expect(resolveWorkspace(account, { selector: " team SPACE ", savedWorkspaceId: "gone" })).toEqual(team);
		expect(resolveWorkspace(account, { selector: "team" })).toEqual(team);
		expect(resolveWorkspace(account, { savedWorkspaceId: "team" })).toEqual(team);
		expect(resolveWorkspace(account)).toEqual(home);
		expect(resolveWorkspace({ ...account, workspaces: [home, { ...team, name: "Renamed" }] }, { savedWorkspaceId: "team" }).name).toBe("Renamed");
	});
	it("rejects ambiguous, substring, missing and revoked choices without falling home", () => {
		expect(() => resolveWorkspace({ ...account, workspaces: [team, { ...team, id: "other" }] }, { selector: "Team Space" })).toThrow("ambiguous");
		for (const selector of ["Team", "gone", ""]) expect(() => resolveWorkspace(account, { selector })).toThrow("unavailable");
		expect(() => resolveWorkspace(account, { savedWorkspaceId: "gone" })).toThrow("saved workspace");
		expect(() => resolveWorkspace({ ...account, homeWorkspaceId: undefined })).toThrow("No home");
	});
	it("partitions keys by account, environment, normalized origin and workspace", () => {
		const scope = registryAccountScope("development", "user", "http://localhost:8080/");
		expect(scope).toBe(registryAccountScope("development", "user", "http://localhost:8080"));
		for (const other of [registryAccountScope("production", "user"), registryAccountScope("development", "other", "http://localhost:8080"), registryAccountScope("development", "user", "http://localhost:8081")]) expect(other).not.toBe(scope);
		expect(registryAgentKey("registry", "agent", scope, "home")).not.toBe(registryAgentKey("registry", "agent", scope, "team"));
	});
	it("round-trips multiple account preferences without modifying unrelated settings", () => {
		const directory = mkdtempSync(join(tmpdir(), "workspaces-"));
		try {
			const file = join(directory, "chat2.json");
			const settings = loadChatSettings(file).settings;
			settings.environments.production.registryAccounts = { first: { workspaceId: "team", selectedAgentKeys: { team: "agent-key" } }, second: { workspaceId: "other" } };
			expect(saveChatSettings(settings, file)).toBeUndefined();
			expect(loadChatSettings(file).settings).toEqual(settings);
		} finally { rmSync(directory, { recursive: true }); }
	});
	it("keeps corrupt saved selections actionable rather than silently defaulting home", () => {
		const preferences = normalizeRegistryPreferences({ account: { workspaceId: 42, selectedAgentKeys: { team: 99 } } });
		expect(preferences).toEqual({ account: { workspaceId: "", selectedAgentKeys: {} } });
		expect(() => resolveWorkspace(account, { savedWorkspaceId: preferences!.account.workspaceId })).toThrow("saved workspace");
	});
	it("adopts only legacy home history owned by the current credential scope", () => {
		const key = createAgentKey("registry", "agent");
		const legacy = { agentKey: key, agentId: "agent", sourceKey: "registry", agentCardUrl: "https://example.test", lastSeenAt: "now", activeContextId: "context", credentialScope: "account:owner" };
		const options = { agents: { [key]: legacy }, sourceKey: "registry", identityId: "agent", workspaceId: "home", homeWorkspaceId: "home", credentialScope: "account:owner" };
		expect(eligibleLegacyRegistryAgent(options)).toEqual(legacy);
		expect(eligibleLegacyRegistryAgent({ ...options, workspaceId: "team" })).toBeUndefined();
		expect(eligibleLegacyRegistryAgent({ ...options, credentialScope: "another" })).toBeUndefined();
		expect(eligibleLegacyRegistryAgent({ ...options, credentialScope: undefined })).toBeUndefined();
	});
});
