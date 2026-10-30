import { describe, expect, it, vi } from "vitest";
import { discoverAgentSources } from "../src/lib/agents/discovery.js";
import { createDefaultRegistryAgentSource } from "../src/lib/agents/model.js";
import { registryAccountScope } from "../src/lib/workspaces.js";

describe("workspace catalog boundary", () => {
	it("uses the selected membership and gives the same identity different workspace keys", async () => {
		const catalogIds: string[] = [];
		const graphQLFetchImpl = vi.fn<typeof fetch>(async (_input, init) => {
			const { query, variables } = JSON.parse(String(init?.body));
			if (query.includes("LoginBootstrap")) return Response.json({ data: { login: {} } });
			if (query.includes("CurrentUser")) return Response.json({ data: { user: {
				id: "account", homeOrganization: { id: "home", name: "Home" },
				memberships: [{ organization: { id: "team", name: "Team" } }]
			} } });
			catalogIds.push(variables.organizationId);
			return Response.json({ data: { agentIdentityDetails: [{ identity: {
				id: "same-identity", name: "Agent", a2aUrl: "https://api.aion.to/agent", atName: "@agent"
			} }] } });
		});
		const options = { environmentId: "production" as const, graphQLFetchImpl, controlPlaneAccessTokenProvider: async () => "test-token" };
		const fetchCard = vi.fn<typeof fetch>(async () => Response.json({ name: "Agent", capabilities: {} }));
		const source = createDefaultRegistryAgentSource("production");
		const home = await discoverAgentSources([source], fetchCard, options);
		const team = await discoverAgentSources([source], fetchCard, { ...options, workspaceSelector: "team" });
		expect(catalogIds).toEqual(["home", "team"]);
		expect(home.agents).toHaveLength(1);
		expect(team.agents).toHaveLength(1);
		expect(home.agents[0].agentKey).not.toBe(team.agents[0].agentKey);
		expect(team.registry?.workspace.id).toBe("team");
		const invalid = await discoverAgentSources([source], fetchCard, { ...options, registryPreferences: {
			[registryAccountScope("production", "account")]: { workspaceId: "revoked" }
		} });
		expect(invalid.agents).toEqual([]);
		expect(invalid.workspaceAccount?.workspaces).toHaveLength(2);
		expect(invalid.errors[0].error).toContain("saved workspace");
		expect(catalogIds).toEqual(["home", "team"]);
	});
});
