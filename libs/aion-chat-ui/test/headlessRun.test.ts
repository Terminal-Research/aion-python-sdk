import type { AgentCard, Message, Part, Task } from "@a2a-js/sdk";
import { describe, expect, it, vi } from "vitest";

import type { HeadlessRunOptions } from "../src/args.js";
import type { ChatSettings } from "../src/lib/chatSettings.js";
import type { ConnectedClient, StreamEvent } from "../src/lib/connection.js";
import { runHeadless } from "../src/lib/headlessRun.js";
import type {
	AgentDiscoveryResult,
	AgentDiscoveryOptions
} from "../src/lib/agents/discovery.js";
import {
	type DiscoveredAgentRecord,
	createExplicitAgentSource,
	createDefaultLocalAgentSource
} from "../src/lib/agents/model.js";

const agentCard: AgentCard = {
	name: "Team Agent",
	description: "Answers team questions.",
	url: "http://localhost:8000/agents/team-agent/",
	version: "1.0.0",
	protocolVersion: "0.3.0",
	capabilities: {},
	defaultInputModes: ["text"],
	defaultOutputModes: ["text"],
	skills: []
};

function createStream(): { stream: { write(chunk: string): void }; output: () => string } {
	let value = "";
	return {
		stream: {
			write(chunk: string): void {
				value += chunk;
			}
		},
		output: () => value
	};
}

function message(
	messageId: string,
	role: "agent" | "user",
	text: string,
	taskId?: string
): Message {
	return {
		kind: "message",
		messageId,
		role,
		...(taskId ? { taskId } : {}),
		parts: [{ kind: "text", text }]
	};
}

function task(overrides: Partial<Task> = {}): Task {
	return {
		kind: "task",
		id: "task-1",
		contextId: "context-1",
		status: { state: "completed" },
		...overrides
	};
}

function discoveredAgent(
	overrides: Partial<DiscoveredAgentRecord> = {}
): DiscoveredAgentRecord {
	const source = createDefaultLocalAgentSource();
	return {
		agentKey: "default-localhost-8000:team-agent",
		agentId: "team-agent",
		id: "team-agent",
		path: "/agents/team-agent",
		sourceKey: source.sourceKey,
		source,
		agentCardUrl:
			"http://localhost:8000/agents/team-agent/.well-known/agent-card.json",
		agentCardName: "Team Agent",
		agentHandle: "@team-agent",
		lastSeenAt: "2026-05-01T00:00:00.000Z",
		lastLoadedAt: "2026-05-01T00:00:00.000Z",
		status: "available",
		connectionUrl: "http://localhost:8000",
		connectionAgentId: "team-agent",
		agentCard,
		...overrides
	};
}

function settings(agent = discoveredAgent()): ChatSettings {
	const source = agent.source;
	return {
		selectedEnvironment: "development",
		environments: {
			production: {
				requestMode: "send-message",
				responseMode: "message-output",
				agentSources: {},
				agents: {}
			},
			staging: {
				requestMode: "send-message",
				responseMode: "message-output",
				agentSources: {},
				agents: {}
			},
			development: {
				requestMode: "send-message",
				responseMode: "message-output",
				selectedAgentKey: agent.agentKey,
				selectedAgentId: agent.id,
				agentSources: {
					[source.sourceKey]: source
				},
				agents: {
					[agent.agentKey]: {
						agentKey: agent.agentKey,
						agentId: agent.agentId,
						sourceKey: agent.sourceKey,
						agentCardUrl: agent.agentCardUrl,
						agentCardName: agent.agentCardName,
						agentHandle: agent.agentHandle,
						lastSeenAt: agent.lastSeenAt,
						lastLoadedAt: agent.lastLoadedAt,
						status: agent.status,
						activeContextId: "context-saved"
					}
				}
			}
		}
	};
}

function options(overrides: Partial<HeadlessRunOptions> = {}): HeadlessRunOptions {
	return {
		headers: {},
		pushNotifications: false,
		pushReceiver: "http://localhost:5000",
		requestMode: "send-message",
		responseMode: "message-output",
		readMessageFromStdin: false,
		message: "hello",
		...overrides
	};
}

function discoveryResult(agent = discoveredAgent()): AgentDiscoveryResult {
	return {
		sources: [agent.source],
		agents: [agent],
		errors: []
	};
}

function buildParts(text: string): Part[] {
	return [{ kind: "text", text }];
}

function connectedClient({
	card = agentCard,
	sendMessage,
	streamEvents = []
}: {
	card?: AgentCard;
	sendMessage?: ConnectedClient["client"]["sendMessage"];
	streamEvents?: StreamEvent[];
}): ConnectedClient {
	const client = {
		sendMessage:
			sendMessage ??
			vi.fn(async () => message("agent-1", "agent", "answer")),
		async *sendMessageStream() {
			for (const event of streamEvents) {
				yield event;
			}
		}
	} as unknown as ConnectedClient["client"];

	return {
		agentCard: card,
		client,
		endpoints: {
			baseUrl: "http://localhost:8000",
			cardUrl: "http://localhost:8000/.well-known/agent-card.json",
			cardPath: "/.well-known/agent-card.json",
			rpcUrl: "http://localhost:8000/"
		}
	};
}

describe("runHeadless", () => {
	it("selects an agent by handle and writes rendered message output", async () => {
		const selected = discoveredAgent();
		const stdout = createStream();
		const stderr = createStream();
		const sendMessage = vi.fn(async () => message("agent-1", "agent", "answer"));
		const connectClientImpl = vi.fn(async () =>
			connectedClient({ sendMessage })
		);

		await expect(
			runHeadless(options({ agentSelector: "@team-agent" }), {
				stdout: stdout.stream,
				stderr: stderr.stream,
				loadChatSettingsImpl: () => ({ settings: settings(selected) }),
				discoverAgentSourcesImpl: async () => discoveryResult(selected),
				connectClientImpl,
				buildMessagePartsImpl: async (text) => buildParts(text),
				getStoredAccessTokenImpl: async () => undefined
			})
		).resolves.toBe(0);

		expect(stdout.output()).toBe("answer\n");
		expect(stderr.output()).toBe("");
		expect(connectClientImpl).toHaveBeenCalledWith(
			expect.objectContaining({
				url: "http://localhost:8000",
				agentId: "team-agent"
			})
		);
		expect(sendMessage).toHaveBeenCalledWith(
			expect.objectContaining({
				message: expect.objectContaining({
					contextId: "context-saved",
					parts: [{ kind: "text", text: "hello" }]
				})
			})
		);
	});

	it("prefers an explicit URL source over saved selection without an agent selector", async () => {
		const saved = discoveredAgent();
		const explicitSource = createExplicitAgentSource("http://localhost:9000");
		const explicitAgent = discoveredAgent({
			agentKey: `${explicitSource.sourceKey}:team-agent`,
			sourceKey: explicitSource.sourceKey,
			source: explicitSource,
			agentCardUrl: "http://localhost:9000/.well-known/agent-card.json",
			connectionUrl: "http://localhost:9000"
		});
		const stdout = createStream();
		const stderr = createStream();
		const connectClientImpl = vi.fn(async () => connectedClient({}));

		await expect(
			runHeadless(options({ url: "http://localhost:9000" }), {
				stdout: stdout.stream,
				stderr: stderr.stream,
				loadChatSettingsImpl: () => ({ settings: settings(saved) }),
				discoverAgentSourcesImpl: async () => ({
					sources: [saved.source, explicitSource],
					agents: [saved, explicitAgent],
					errors: []
				}),
				connectClientImpl,
				buildMessagePartsImpl: async (text) => buildParts(text),
				getStoredAccessTokenImpl: async () => undefined
			})
		).resolves.toBe(0);

		expect(connectClientImpl).toHaveBeenCalledWith(
			expect.objectContaining({
				url: "http://localhost:9000"
			})
		);
		expect(stdout.output()).toBe("answer\n");
		expect(stderr.output()).toBe("");
	});

	it("writes raw A2A JSON for message sends in a2a response mode", async () => {
		const stdout = createStream();
		const stderr = createStream();

		await runHeadless(options({ responseMode: "a2a-protocol" }), {
			stdout: stdout.stream,
			stderr: stderr.stream,
			loadChatSettingsImpl: () => ({ settings: settings() }),
			discoverAgentSourcesImpl: async () => discoveryResult(),
			connectClientImpl: async () =>
				connectedClient({
					sendMessage: vi.fn(async () => message("agent-1", "agent", "answer"))
				}),
			buildMessagePartsImpl: async (text) => buildParts(text),
			getStoredAccessTokenImpl: async () => undefined
		});

		expect(JSON.parse(stdout.output())).toMatchObject({
			kind: "message",
			role: "agent",
			parts: [{ kind: "text", text: "answer" }]
		});
		expect(stderr.output()).toBe("");
	});

	it("renders status messages from terminal tasks that also contain user history", async () => {
		const stdout = createStream();
		const stderr = createStream();

		await runHeadless(options(), {
			stdout: stdout.stream,
			stderr: stderr.stream,
			loadChatSettingsImpl: () => ({ settings: settings() }),
			discoverAgentSourcesImpl: async () => discoveryResult(),
			connectClientImpl: async () =>
				connectedClient({
					sendMessage: vi.fn(async () =>
						task({
							history: [message("user-1", "user", "question", "task-1")],
							status: {
								state: "completed",
								message: message("agent-1", "agent", "answer", "task-1")
							}
						})
					)
				}),
			buildMessagePartsImpl: async (text) => buildParts(text),
			getStoredAccessTokenImpl: async () => undefined
		});

		expect(stdout.output()).toBe("answer\n");
		expect(stderr.output()).toBe("");
	});

	it("suppresses duplicate final status messages after stream deltas", async () => {
		const stdout = createStream();
		const stderr = createStream();
		const streamingCard = {
			...agentCard,
			capabilities: { streaming: true }
		};

		await runHeadless(options({ requestMode: "streaming-message" }), {
			stdout: stdout.stream,
			stderr: stderr.stream,
			loadChatSettingsImpl: () => ({ settings: settings() }),
			discoverAgentSourcesImpl: async () => discoveryResult(),
			connectClientImpl: async () =>
				connectedClient({
					card: streamingCard,
					streamEvents: [
						{
							kind: "artifact-update",
							taskId: "task-1",
							contextId: "context-1",
							append: true,
							artifact: {
								artifactId: "aion:stream-delta",
								parts: [{ kind: "text", text: "hel" }]
							}
						},
						{
							kind: "artifact-update",
							taskId: "task-1",
							contextId: "context-1",
							append: true,
							artifact: {
								artifactId: "aion:stream-delta",
								parts: [{ kind: "text", text: "lo" }]
							}
						},
						{
							kind: "status-update",
							taskId: "task-1",
							contextId: "context-1",
							final: true,
							status: {
								state: "completed",
								message: message("agent-1", "agent", "hello", "task-1")
							}
						}
					]
				}),
			buildMessagePartsImpl: async (text) => buildParts(text),
			getStoredAccessTokenImpl: async () => undefined
		});

		expect(stdout.output()).toBe("hello\n");
		expect(stderr.output()).toBe("");
	});

	it("renders terminal status messages after empty stream deltas", async () => {
		const stdout = createStream();
		const stderr = createStream();
		const streamingCard = {
			...agentCard,
			capabilities: { streaming: true }
		};

		await runHeadless(options({ requestMode: "streaming-message" }), {
			stdout: stdout.stream,
			stderr: stderr.stream,
			loadChatSettingsImpl: () => ({ settings: settings() }),
			discoverAgentSourcesImpl: async () => discoveryResult(),
			connectClientImpl: async () =>
				connectedClient({
					card: streamingCard,
					streamEvents: [
						{
							kind: "artifact-update",
							taskId: "task-1",
							contextId: "context-1",
							append: true,
							artifact: {
								artifactId: "aion:stream-delta",
								parts: [{ kind: "text", text: "" }]
							}
						},
						{
							kind: "status-update",
							taskId: "task-1",
							contextId: "context-1",
							final: true,
							status: {
								state: "completed",
								message: message("agent-1", "agent", "answer", "task-1")
							}
						}
					]
				}),
			buildMessagePartsImpl: async (text) => buildParts(text),
			getStoredAccessTokenImpl: async () => undefined
		});

		expect(stdout.output()).toBe("answer\n");
		expect(stderr.output()).toBe("");
	});

	it("falls back to send-message when streaming is not supported", async () => {
		const stdout = createStream();
		const stderr = createStream();

		await runHeadless(options({ requestMode: "streaming-message" }), {
			stdout: stdout.stream,
			stderr: stderr.stream,
			loadChatSettingsImpl: () => ({ settings: settings() }),
			discoverAgentSourcesImpl: async () => discoveryResult(),
			connectClientImpl: async () =>
				connectedClient({
					sendMessage: vi.fn(async () => message("agent-1", "agent", "answer"))
				}),
			buildMessagePartsImpl: async (text) => buildParts(text),
			getStoredAccessTokenImpl: async () => undefined
		});

		expect(stdout.output()).toBe("answer\n");
		expect(stderr.output()).toContain("Request mode fallback");
	});

	it("reports terminal tasks with no agent output on stderr", async () => {
		const stdout = createStream();
		const stderr = createStream();

		await runHeadless(options(), {
			stdout: stdout.stream,
			stderr: stderr.stream,
			loadChatSettingsImpl: () => ({ settings: settings() }),
			discoverAgentSourcesImpl: async () => discoveryResult(),
			connectClientImpl: async () =>
				connectedClient({
					sendMessage: vi.fn(async () =>
						task({
							history: [message("user-1", "user", "question", "task-1")]
						})
					)
				}),
			buildMessagePartsImpl: async (text) => buildParts(text),
			getStoredAccessTokenImpl: async () => undefined
		});

		expect(stdout.output()).toBe("");
		expect(stderr.output()).toBe("Task completed with no agent message.\n");
	});

	it("passes CLI auth only to explicit source fetches during discovery", async () => {
		const explicitFetchCalls: string[] = [];
		const defaultFetchCalls: string[] = [];
		const source = discoveredAgent().source;
		const explicitSourceKey = createExplicitAgentSource(
			"http://localhost:9000"
		).sourceKey;
		const stdout = createStream();
		const stderr = createStream();
		const discoverAgentSourcesImpl = vi.fn(
			async (
				sources,
				_fetchImpl,
				discoveryOptions?: AgentDiscoveryOptions
			): Promise<AgentDiscoveryResult> => {
				for (const runtimeSource of sources) {
					const sourceFetch = discoveryOptions?.sourceFetchImpl?.(runtimeSource);
					await sourceFetch?.(`http://example.com/${runtimeSource.sourceKey}`);
				}
				return discoveryResult();
			}
		);
		const fetchImpl = vi.fn(async (url: string | URL | Request) => {
			defaultFetchCalls.push(String(url));
			return new Response("", { status: 404 });
		}) as unknown as typeof fetch;
		const originalFetch = globalThis.fetch;
		globalThis.fetch = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
			const headers = new Headers(init?.headers);
			if (headers.get("Authorization") === "Bearer explicit-token") {
				explicitFetchCalls.push(String(url));
			}
			return new Response("", { status: 404 });
		}) as unknown as typeof fetch;

		try {
			const baseSettings = settings();
			await runHeadless(
				options({
					url: "http://localhost:9000",
					token: "explicit-token"
				}),
				{
					stdout: stdout.stream,
					stderr: stderr.stream,
					fetchImpl,
					loadChatSettingsImpl: () => ({
						settings: {
							...baseSettings,
							environments: {
								...baseSettings.environments,
								development: {
									...baseSettings.environments.development,
									agentSources: { [source.sourceKey]: source }
								}
							}
						}
					}),
					discoverAgentSourcesImpl,
					connectClientImpl: async () => connectedClient({}),
					buildMessagePartsImpl: async (text) => buildParts(text),
					getStoredAccessTokenImpl: async () => undefined
				}
			);
		} finally {
			globalThis.fetch = originalFetch;
		}

		expect(defaultFetchCalls).toContain("http://example.com/default-localhost-8000");
		expect(explicitFetchCalls.some((url) => url.includes(explicitSourceKey))).toBe(true);
	});
});
