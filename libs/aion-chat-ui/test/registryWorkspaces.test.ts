import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { parse, print } from "graphql";
import { CURRENT_USER_QUERY, AGENT_CATALOG_IDENTITIES_QUERY, fetchRegistryAccount, fetchRegistryAgentIdentities } from "../src/lib/graphql/registry.js";

const respond = (data: unknown) => new Response(JSON.stringify({ data }));
const home = { id: "home", name: "Personal", organizationType: "Personal" };
const team = { id: "team", name: "Team", organizationType: "Team" };
const user = { id: "user", homeOrganization: home, agentIdentity: { id: "self", organizationId: "home", a2aUrl: "https://api.aion.to/self" } };
const options = (fetchImpl: typeof fetch) => ({ environmentId: "production" as const, accessToken: "test-token", fetchImpl });

describe("registry workspaces", () => {
	it("normalizes memberships and deduplicates the home workspace", async () => {
		const fetchImpl = vi.fn().mockResolvedValueOnce(respond({ login: {} }))
			.mockResolvedValueOnce(respond({ user: { ...user, memberships: [{ organization: team }, { organization: home }] } }));
		expect(await fetchRegistryAccount(options(fetchImpl))).toEqual({ userId: "user", homeWorkspaceId: "home", workspaces: [home, team] });
		expect(JSON.parse(String(fetchImpl.mock.calls[1][1]?.body)).query).toContain("memberships");
	});
	it("accepts nullable membership and home fields without inventing membership", async () => {
		const fetchImpl = vi.fn().mockResolvedValueOnce(respond({ login: {} }))
			.mockResolvedValueOnce(respond({ user: { ...user, homeOrganization: null, memberships: null } }));
		expect(await fetchRegistryAccount(options(fetchImpl))).toEqual({ userId: "user", workspaces: [] });
	});
	it("surfaces membership query failure instead of an empty workspace list", async () => {
		const fetchImpl = vi.fn().mockResolvedValueOnce(respond({ login: {} }))
			.mockResolvedValueOnce(new Response(JSON.stringify({ errors: [{ message: "Membership lookup failed" }] })));
		await expect(fetchRegistryAccount(options(fetchImpl))).rejects.toThrow("Membership lookup failed");
	});
	it("queries the requested organization and treats an empty catalog as authoritative", async () => {
		const fetchImpl = vi.fn().mockResolvedValue(respond({ agentIdentityDetails: [] }));
		expect(await fetchRegistryAgentIdentities({ ...options(fetchImpl), organizationId: "team" })).toEqual([]);
		expect(JSON.parse(String(fetchImpl.mock.calls[0][1]?.body)).variables).toEqual({ organizationId: "team", networkTypes: ["A2A", "AionChat"] });
		expect(fetchImpl).toHaveBeenCalledTimes(1);
	});
	it("keeps runtime and codegen operations identical", () => {
		const documents = parse(readFileSync(new URL("../src/graphql/operations/registry.graphql", import.meta.url), "utf8"));
		for (const query of [CURRENT_USER_QUERY, AGENT_CATALOG_IDENTITIES_QUERY]) {
			const operation = parse(query).definitions[0];
			expect(documents.definitions.map((item) => print(item))).toContain(print(operation));
		}
	});
});
