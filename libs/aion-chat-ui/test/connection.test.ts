import { describe, expect, it } from "vitest";

import type { ChatCliOptions } from "../src/args.js";
import {
	buildEndpointConfig,
	buildMessageParams,
	createPushNotificationConfig
} from "../src/lib/connection.js";

function buildOptions(overrides: Partial<ChatCliOptions> = {}): ChatCliOptions {
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
