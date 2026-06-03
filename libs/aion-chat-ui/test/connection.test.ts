import { afterEach, describe, expect, it, vi } from "vitest";

import {
	buildAuthenticatedFetch,
	buildEndpointConfig,
	buildMessageParams,
	normalizeAgentCardTransports,
	type ChatConnectionOptions,
	createPushNotificationConfig
} from "../src/lib/connection.js";

function buildOptions(overrides: Partial<ChatConnectionOptions> = {}): ChatConnectionOptions {
	return {
		url: "http://localhost:8000",
		agentId: undefined,
		token: undefined,
		headers: {},
		pushNotifications: false,
		pushReceiver: "http://localhost:5000",
		...overrides
	};
}

afterEach(() => {
	vi.unstubAllGlobals();
});

describe("buildEndpointConfig", () => {
	it("derives direct agent-card endpoints from a base URL", () => {
		expect(buildEndpointConfig(buildOptions())).toEqual({
			baseUrl: "http://localhost:8000",
			cardUrl: "http://localhost:8000/.well-known/agent-card.json",
			cardPath: "/.well-known/agent-card.json",
			rpcUrl: "http://localhost:8000/"
		});
	});

	it("accepts direct agent-card URLs", () => {
		expect(
			buildEndpointConfig(
				buildOptions({
					url: "http://localhost:8000/.well-known/agent-card.json"
				})
			)
		).toEqual({
			baseUrl: "http://localhost:8000/.well-known/agent-card.json",
			cardUrl: "http://localhost:8000/.well-known/agent-card.json",
			cardPath: "",
			rpcUrl: "http://localhost:8000/"
		});
	});

	it("rewrites endpoints for proxy-aware agent routing", () => {
		expect(
			buildEndpointConfig(
				buildOptions({
					url: "http://localhost:8000",
					agentId: "demo-agent"
				})
			)
		).toEqual({
			baseUrl: "http://localhost:8000",
			cardUrl: "http://localhost:8000/agents/demo-agent/.well-known/agent-card.json",
			cardPath: "/agents/demo-agent/.well-known/agent-card.json",
			rpcUrl: "http://localhost:8000/agents/demo-agent/"
		});
	});
});

describe("normalizeAgentCardTransports", () => {
	it("maps A2A 1.0 supported interfaces into SDK transport fields", () => {
		const agentCard = {
			name: "Prompt Agent",
			description: "Prompt-driven agent.",
			supportedInterfaces: [
				{
					url: "http://localhost:8080/distributions/demo/a2a",
					protocolBinding: "HTTP+JSON",
					protocolVersion: "1.0",
					tenant: null
				},
				{
					url: "http://localhost:8080/distributions/demo/a2a/rpc",
					protocolBinding: "JSONRPC",
					protocolVersion: "1.0",
					tenant: null
				}
			],
			version: "1.0.0",
			protocolVersion: "1.0",
			capabilities: {},
			defaultInputModes: ["application/json", "text/plain"],
			defaultOutputModes: ["application/json", "text/plain"],
			skills: []
		};

		const normalized = normalizeAgentCardTransports(
			agentCard as unknown as Parameters<typeof normalizeAgentCardTransports>[0]
		);

		expect(normalized.url).toBe("http://localhost:8080/distributions/demo/a2a");
		expect(normalized.preferredTransport).toBe("HTTP+JSON");
		expect(normalized.additionalInterfaces).toEqual([
			{
				url: "http://localhost:8080/distributions/demo/a2a",
				transport: "HTTP+JSON"
			},
			{
				url: "http://localhost:8080/distributions/demo/a2a/rpc",
				transport: "JSONRPC"
			}
		]);
	});

	it("preserves existing SDK transport fields", () => {
		const agentCard = {
			name: "Team Agent",
			description: "Answers team questions.",
			url: "http://localhost:8000/a2a",
			preferredTransport: "JSONRPC",
			additionalInterfaces: [
				{ url: "http://localhost:8000/a2a", transport: "JSONRPC" }
			],
			version: "1.0.0",
			protocolVersion: "0.3.0",
			capabilities: {},
			defaultInputModes: ["text"],
			defaultOutputModes: ["text"],
			skills: []
		};

		expect(normalizeAgentCardTransports(agentCard)).toEqual(agentCard);
	});
});

describe("buildAuthenticatedFetch", () => {
	it("adds custom headers and an explicit bearer token without calling the token provider", async () => {
		const tokenProvider = vi.fn(async () => "stored-token");
		const fetchMock = vi.fn(async () => new Response("ok"));
		vi.stubGlobal("fetch", fetchMock);

		const fetchImpl = buildAuthenticatedFetch({
			headers: { "X-Test": "one" },
			token: "explicit-token",
			tokenProvider
		});
		await fetchImpl("http://localhost:8000/.well-known/manifest.json");

		const calls = fetchMock.mock.calls as unknown as Array<
			[RequestInfo | URL, RequestInit?]
		>;
		const [, init] = calls[0] ?? [];
		const headers = (init as RequestInit).headers as Headers;
		expect(headers.get("Authorization")).toBe("Bearer explicit-token");
		expect(headers.get("X-Test")).toBe("one");
		expect(tokenProvider).not.toHaveBeenCalled();
	});

	it("uses the token provider when no explicit token is supplied", async () => {
		const tokenProvider = vi.fn(async () => "stored-token");
		const fetchMock = vi.fn(async () => new Response("ok"));
		vi.stubGlobal("fetch", fetchMock);

		const fetchImpl = buildAuthenticatedFetch({
			headers: {},
			tokenProvider
		});
		await fetchImpl("http://localhost:8000/.well-known/manifest.json");

		const calls = fetchMock.mock.calls as unknown as Array<
			[RequestInfo | URL, RequestInit?]
		>;
		const [, init] = calls[0] ?? [];
		const headers = (init as RequestInit).headers as Headers;
		expect(headers.get("Authorization")).toBe("Bearer stored-token");
		expect(tokenProvider).toHaveBeenCalledOnce();
	});
});

describe("buildMessageParams", () => {
	it("includes push configuration only when requested", () => {
		const withoutPush = buildMessageParams([{ kind: "text", text: "hello" }], "context-1", "task-1");
		expect(withoutPush.configuration).toEqual({
			acceptedOutputModes: ["text", "text/plain", "application/json"]
		});

		const pushConfig = createPushNotificationConfig("http://127.0.0.1:5000");
		const withPush = buildMessageParams(
			[{ kind: "text", text: "hello" }],
			"context-1",
			"task-1",
			pushConfig
		);
		expect(withPush.configuration).toEqual({
			acceptedOutputModes: ["text", "text/plain", "application/json"],
			pushNotificationConfig: pushConfig
		});
	});
});
