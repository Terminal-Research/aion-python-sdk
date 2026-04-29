import { describe, expect, it, vi } from "vitest";

import {
	discoverAgentSources,
	selectDiscoveredAgent
} from "../src/lib/agents/discovery.js";
import {
	type AgentSourceRecord,
	type DiscoveredAgentRecord,
	createDefaultLocalAgentSource,
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
