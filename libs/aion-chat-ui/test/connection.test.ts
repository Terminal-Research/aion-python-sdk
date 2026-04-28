import { afterEach, describe, expect, it, vi } from "vitest";

import {
	buildAuthenticatedFetch,
	buildEndpointConfig,
	buildMessageParams,
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
		const withoutPush = buildMessageParams("hello", "context-1", "task-1");
		expect(withoutPush.configuration).toEqual({
			acceptedOutputModes: ["text"]
		});

		const pushConfig = createPushNotificationConfig("http://127.0.0.1:5000");
		const withPush = buildMessageParams(
			"hello",
			"context-1",
			"task-1",
			pushConfig
		);
		expect(withPush.configuration).toEqual({
			acceptedOutputModes: ["text"],
			pushNotificationConfig: pushConfig
		});
	});
});
