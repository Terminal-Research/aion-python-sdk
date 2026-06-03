import type { AgentCard, Message, Part, StreamResponse, Task } from "@a2a-js/sdk";
import { Role, TaskState } from "@a2a-js/sdk";
import { describe, expect, it, vi } from "vitest";

import type { HeadlessRunOptions } from "../src/args.js";
import type { ChatSettings } from "../src/lib/chatSettings.js";
import type { ConnectedClient } from "../src/lib/connection.js";
import { makeTextPart } from "../src/lib/a2aProtocol.js";
import { runHeadless } from "../src/lib/headlessRun.js";
import type {
	AgentDiscoveryResult,
	AgentDiscoveryOptions
} from "../src/lib/agents/discovery.js";
import {
	type DiscoveredAgentRecord,
	createExplicitAgentSource,
	createDefaultLocalAgentSource,
	createDefaultRegistryAgentSource
} from "../src/lib/agents/model.js";

const agentCard: AgentCard = {
	name: "Team Agent",
	description: "Answers team questions.",
	supportedInterfaces: [
		{
			url: "http://localhost:8000/agents/team-agent/",
			protocolBinding: "JSONRPC",
			protocolVersion: "1.0",
			tenant: ""
		}
	],
	provider: undefined,
	version: "1.0.0",
	documentationUrl: undefined,
	capabilities: { extensions: [] },
	securitySchemes: {},
	securityRequirements: [],
	defaultInputModes: ["text/plain"],
	defaultOutputModes: ["text/plain"],
	skills: [],
	signatures: [],
	iconUrl: undefined
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
		messageId,
		contextId: "context-1",
		taskId: taskId ?? "",
		role: role === "agent" ? Role.ROLE_AGENT : Role.ROLE_USER,
		parts: [makeTextPart(text)],
		metadata: undefined,
		extensions: [],
		referenceTaskIds: []
	};
}

function task(overrides: Partial<Task> = {}): Task {
	return {
		id: "task-1",
		contextId: "context-1",
		status: {
			state: TaskState.TASK_STATE_COMPLETED,
			message: undefined,
			timestamp: undefined
		},
		artifacts: [],
		history: [],
		metadata: undefined,
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
	return [makeTextPart(text)];
}

function connectedClient({
	card = agentCard,
	sendMessage,
	streamEvents = []
}: {
	card?: AgentCard;
	sendMessage?: ConnectedClient["client"]["sendMessage"];
	streamEvents?: StreamResponse[];
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

function streamArtifactUpdate(text: string): StreamResponse {
	return {
		payload: {
			$case: "artifactUpdate",
			value: {
				taskId: "task-1",
				contextId: "context-1",
				append: true,
				lastChunk: false,
				metadata: undefined,
				artifact: {
					artifactId: "aion:stream-delta",
					name: "",
					description: "",
					parts: [makeTextPart(text)],
					metadata: undefined,
					extensions: []
				}
			}
		}
	};
}

function streamStatusUpdate(text: string): StreamResponse {
	return {
		payload: {
			$case: "statusUpdate",
			value: {
				taskId: "task-1",
				contextId: "context-1",
				status: {
					state: TaskState.TASK_STATE_COMPLETED,
					message: message("agent-1", "agent", text, "task-1"),
					timestamp: undefined
				},
				metadata: undefined
			}
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
					parts: [makeTextPart("hello")]
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
			role: Role.ROLE_AGENT,
			parts: [{ content: { $case: "text", value: "answer" } }]
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
								state: TaskState.TASK_STATE_COMPLETED,
								message: message("agent-1", "agent", "answer", "task-1"),
								timestamp: undefined
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
			capabilities: { extensions: [], streaming: true }
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
						streamArtifactUpdate("hel"),
						streamArtifactUpdate("lo"),
						streamStatusUpdate("hello")
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
			capabilities: { extensions: [], streaming: true }
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
						streamArtifactUpdate(""),
						streamStatusUpdate("answer")
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

	it("passes Aion registry auth only to built-in control-plane registry agents", async () => {
		const registrySource = createDefaultRegistryAgentSource("development");
		const registryAgent = discoveredAgent({
			agentKey: `${registrySource.sourceKey}:prompt-agent`,
			agentId: "identity-1",
			id: "prompt-agent",
			sourceKey: registrySource.sourceKey,
			source: registrySource,
			agentCardUrl:
				"http://localhost:8080/distributions/demo/a2a/.well-known/agent-card.json",
			connectionUrl:
				"http://localhost:8080/distributions/demo/a2a/.well-known/agent-card.json",
			connectionAgentId: undefined
		});
		const stdout = createStream();
		const stderr = createStream();
		const getStoredAccessTokenImpl = vi.fn(async () => "registry-token");
		const connectClientImpl = vi.fn(async (connectionOptions) => {
			expect(await connectionOptions.tokenProvider?.()).toBe("registry-token");
			return connectedClient({});
		});

		await expect(
			runHeadless(options({ agentSelector: "@prompt-agent" }), {
				stdout: stdout.stream,
				stderr: stderr.stream,
				loadChatSettingsImpl: () => ({ settings: settings(registryAgent) }),
				discoverAgentSourcesImpl: async () => discoveryResult(registryAgent),
				connectClientImpl,
				buildMessagePartsImpl: async (text) => buildParts(text),
				getStoredAccessTokenImpl
			})
		).resolves.toBe(0);

		expect(getStoredAccessTokenImpl).toHaveBeenCalledWith("development");
		expect(connectClientImpl).toHaveBeenCalledWith(
			expect.objectContaining({
				token: undefined,
				headers: {},
				tokenProvider: expect.any(Function)
			})
		);
	});

	it("does not pass Aion registry auth to arbitrary registry sources", async () => {
		const customRegistrySource = {
			...createDefaultRegistryAgentSource("development"),
			sourceKey: "custom-registry",
			url: "http://example.com"
		};
		const registryAgent = discoveredAgent({
			agentKey: `${customRegistrySource.sourceKey}:prompt-agent`,
			agentId: "identity-1",
			id: "prompt-agent",
			sourceKey: customRegistrySource.sourceKey,
			source: customRegistrySource,
			agentCardUrl: "http://example.com/agents/prompt/.well-known/agent-card.json",
			connectionUrl: "http://example.com/agents/prompt/.well-known/agent-card.json",
			connectionAgentId: undefined
		});
		const stdout = createStream();
		const stderr = createStream();
		const getStoredAccessTokenImpl = vi.fn(async () => "registry-token");
		const connectClientImpl = vi.fn(async () => connectedClient({}));

		await expect(
			runHeadless(options({ agentSelector: "@prompt-agent" }), {
				stdout: stdout.stream,
				stderr: stderr.stream,
				loadChatSettingsImpl: () => ({ settings: settings(registryAgent) }),
				discoverAgentSourcesImpl: async () => discoveryResult(registryAgent),
				connectClientImpl,
				buildMessagePartsImpl: async (text) => buildParts(text),
				getStoredAccessTokenImpl
			})
		).resolves.toBe(0);

		expect(connectClientImpl).toHaveBeenCalledWith(
			expect.objectContaining({ tokenProvider: undefined })
		);
		expect(getStoredAccessTokenImpl).not.toHaveBeenCalled();
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
