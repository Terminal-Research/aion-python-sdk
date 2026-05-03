import { describe, expect, it, vi } from "vitest";

import {
	discoverAgentSources,
	selectDiscoveredAgent
} from "../src/lib/agents/discovery.js";
import {
	type AgentSourceRecord,
	type DiscoveredAgentRecord,
	createDefaultLocalAgentSource,
	createDefaultRegistryAgentSource,
	createExplicitAgentSource,
	isTransientAgentSource
} from "../src/lib/agents/model.js";

const agentCard = {
	name: "Command Agent",
	description: "Runs commands.",
	url: "http://localhost:8000/agents/command-agent/",
	version: "1.0.0",
	protocolVersion: "0.3.0",
	capabilities: {},
	defaultInputModes: ["text"],
	defaultOutputModes: ["text"],
	skills: []
};

function discoveredAgent(
	id: string,
	sourceKey: string
): DiscoveredAgentRecord {
	const source: AgentSourceRecord = {
		sourceKey,
		type: "manifest",
		url: `http://localhost/${sourceKey}`,
		description: sourceKey,
		enabled: true
	};
	return {
		agentKey: `${sourceKey}:${id}`,
		agentId: id,
		id,
		path: `/agents/${id}`,
		sourceKey,
		source,
		agentCardUrl: `http://localhost/${sourceKey}/${id}/.well-known/agent-card.json`,
		lastSeenAt: "2026-04-29T00:00:00.000Z",
		status: "available",
		connectionUrl: `http://localhost/${sourceKey}`,
		connectionAgentId: id
	};
}

describe("discoverAgentSources", () => {
	it("discovers manifest agents and loads their cards", async () => {
		const fetchImpl = vi.fn(async (url: string | URL | Request) => {
			const nextUrl = String(url);
			if (nextUrl === "http://localhost:8000/.well-known/manifest.json") {
				return new Response(
					JSON.stringify({
						endpoints: {
							"command-agent": "/agents/command-agent"
						}
					}),
					{ status: 200 }
				);
			}
			if (
				nextUrl ===
				"http://localhost:8000/agents/command-agent/.well-known/agent-card.json"
			) {
				return new Response(JSON.stringify(agentCard), { status: 200 });
			}
			return new Response("", { status: 404 });
		}) as unknown as typeof fetch;

		const result = await discoverAgentSources(
			[createDefaultLocalAgentSource()],
			fetchImpl
		);

		expect(result.errors).toEqual([]);
		expect(result.sources[0]).toMatchObject({
			sourceKey: "default-localhost-8000",
			type: "manifest",
			status: "available"
		});
		expect(result.agents[0]).toMatchObject({
			id: "command-agent",
			agentCardName: "Command Agent",
			connectionUrl: "http://localhost:8000",
			connectionAgentId: "command-agent"
		});
	});

	it("falls back from explicit manifest lookup to direct agent-card lookup", async () => {
		const fetchImpl = vi.fn(async (url: string | URL | Request) => {
			const nextUrl = String(url);
			if (nextUrl === "http://localhost:9000/.well-known/agent-card.json") {
				return new Response(
					JSON.stringify({
						...agentCard,
						name: "Direct Agent",
						url: "http://localhost:9000/"
					}),
					{ status: 200 }
				);
			}
			return new Response("", { status: 404 });
		}) as unknown as typeof fetch;

		const result = await discoverAgentSources(
			[createExplicitAgentSource("http://localhost:9000")],
			fetchImpl
		);

		expect(result.errors).toEqual([]);
		expect(result.sources[0]).toMatchObject({
			type: "agentCard",
			status: "available",
			description: "Provided with --url"
		});
		expect(result.agents[0]).toMatchObject({
			id: "Direct Agent",
			connectionUrl: "http://localhost:9000/.well-known/agent-card.json"
		});
		expect(isTransientAgentSource(result.sources[0])).toBe(true);
	});

	it("uses the resolved direct card URL instead of the card rpc URL for connection", async () => {
		const fetchImpl = vi.fn(async (url: string | URL | Request) => {
			const nextUrl = String(url);
			if (
				nextUrl ===
				"http://localhost:9000/agents/direct/.well-known/agent-card.json"
			) {
				return new Response(
					JSON.stringify({
						...agentCard,
						name: "Nested Direct Agent",
						url: "http://localhost:9000/rpc/direct"
					}),
					{ status: 200 }
				);
			}
			return new Response("", { status: 404 });
		}) as unknown as typeof fetch;

		const result = await discoverAgentSources(
			[
				createExplicitAgentSource(
					"http://localhost:9000/agents/direct/.well-known/agent-card.json"
				)
			],
			fetchImpl
		);

		expect(result.errors).toEqual([]);
		expect(result.sources[0]).toMatchObject({
			type: "agentCard",
			url: "http://localhost:9000/agents/direct/.well-known/agent-card.json"
		});
		expect(result.agents[0]).toMatchObject({
			id: "Nested Direct Agent",
			connectionUrl:
				"http://localhost:9000/agents/direct/.well-known/agent-card.json"
		});
	});

	it("records explicit source errors without throwing", async () => {
		const fetchImpl = vi.fn(async () => new Response("", { status: 404 })) as unknown as typeof fetch;

		const result = await discoverAgentSources(
			[createExplicitAgentSource("http://localhost:9000")],
			fetchImpl
		);

		expect(result.agents).toEqual([]);
		expect(result.errors[0]?.source).toMatchObject({
			type: "manifest",
			status: "unavailable",
			description: "Provided with --url"
		});
	});

	it("discovers registry identities through GraphQL and direct agent cards", async () => {
		const source = createDefaultRegistryAgentSource("development");
		const graphQLFetchImpl = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
			const nextUrl = String(url);
			if (nextUrl === "http://localhost:8080/api/graphql") {
				const body = JSON.parse(String(init?.body)) as { query: string };
				if (body.query.includes("query LoginBootstrap")) {
					return new Response(
						JSON.stringify({
							data: {
								login: {
									nextRoute: null,
									email: null,
									name: null
								}
							}
						}),
						{ status: 200 }
					);
				}
				if (body.query.includes("query CurrentUser")) {
					return new Response(
						JSON.stringify({
							data: {
								user: {
									id: "user-1",
									homeOrganization: { id: "org-1", name: "Org" },
									agentIdentity: {
										id: "identity-personal",
										name: "Personal Agent",
										atName: "@me",
										a2aUrl: null,
										agentType: "Personal",
										organizationId: "org-1",
										updatedAt: "2026-05-01T00:00:00Z"
									}
								}
							}
						}),
						{ status: 200 }
					);
				}
				return new Response(
					JSON.stringify({
						data: {
							agentIdentityDetails: [
								{
									identity: {
										id: "identity-team",
										name: "Team Agent",
										atName: "@team-agent",
										a2aUrl: "http://registry.example/agents/team-agent",
										agentType: "Principal",
										userId: null,
										organizationId: "org-1",
										systemKey: null,
										website: null,
										email: null,
										biography: null,
										avatarImageUrl: null,
										backgroundImageUrl: null,
										updatedAt: "2026-05-01T00:00:00Z",
										notes: null
									},
									distributionUsages: [
										{ distributionId: "dist-1", networkType: "A2A" }
									]
								}
							]
						}
					}),
					{ status: 200 }
				);
			}
			return new Response("", { status: 404 });
		}) as unknown as typeof fetch;
		const agentCardFetchImpl = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
			const nextUrl = String(url);
			if (
				nextUrl ===
				"http://registry.example/agents/team-agent/.well-known/agent-card.json"
			) {
				const headers = new Headers(init?.headers);
				expect(headers.get("Authorization")).toBeNull();
				return new Response(JSON.stringify(agentCard), { status: 200 });
			}
			return new Response("", { status: 404 });
		}) as unknown as typeof fetch;
		const appAuthenticatedFetch = vi.fn(
			async (url: string | URL | Request, init?: RequestInit) => {
				const headers = new Headers(init?.headers);
				headers.set("Authorization", "Bearer cli-token");
				return agentCardFetchImpl(url, { ...init, headers });
			}
		) as unknown as typeof fetch;

		const result = await discoverAgentSources([source], appAuthenticatedFetch, {
			environmentId: "development",
			controlPlaneAccessTokenProvider: async () => "access-token",
			graphQLFetchImpl,
			sourceFetchImpl: () => agentCardFetchImpl
		});

		expect(result.errors).toEqual([]);
		expect(result.sources[0]).toMatchObject({
			type: "registry",
			status: "available"
		});
		expect(result.agents[0]).toMatchObject({
			agentId: "identity-team",
			id: "team-agent",
			agentHandle: "@team-agent",
			connectionUrl:
				"http://registry.example/agents/team-agent/.well-known/agent-card.json"
		});
		expect(appAuthenticatedFetch).not.toHaveBeenCalled();
	});

	it("prefers an explicit source when selecting a requested agent id", () => {
		const selected = selectDiscoveredAgent(
			[
				discoveredAgent("command-agent", "default-localhost-8000"),
				discoveredAgent("command-agent", "cli-explicit")
			],
			{
				requestedAgentId: "command-agent",
				explicitSourceKey: "cli-explicit"
			}
		);

		expect(selected?.sourceKey).toBe("cli-explicit");
	});
});
