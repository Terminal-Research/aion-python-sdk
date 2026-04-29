import { randomUUID } from "node:crypto";

import type {
	AgentCard,
	Message,
	MessageSendParams,
	Part,
	PushNotificationConfig,
	Task,
	TaskArtifactUpdateEvent,
	TaskStatusUpdateEvent
} from "@a2a-js/sdk";
import {
	ClientFactory,
	ClientFactoryOptions,
	DefaultAgentCardResolver,
	JsonRpcTransportFactory,
	type Client
} from "@a2a-js/sdk/client";

import type { ChatCliOptions } from "../args.js";
import { generateTaskMetadata } from "./a2aMetadata.js";

export interface EndpointConfig {
	baseUrl: string;
	cardUrl: string;
	cardPath: string;
	rpcUrl: string;
}

export interface ConnectedClient {
	agentCard: AgentCard;
	client: Client;
	endpoints: EndpointConfig;
}

export type StreamEvent = Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent;

const AGENT_CARD_PATH = "/.well-known/agent-card.json";

function normalizeEndpoint(url: string): string {
	return url.endsWith("/") ? url.slice(0, -1) : url;
}

function buildDirectEndpoints(url: string): EndpointConfig {
	const normalized = normalizeEndpoint(url);

	if (normalized.endsWith(AGENT_CARD_PATH)) {
		const rpcBaseUrl = normalized.slice(0, -AGENT_CARD_PATH.length);
		return {
			baseUrl: normalized,
			cardUrl: normalized,
			cardPath: "",
			rpcUrl: `${rpcBaseUrl}/`
		};
	}

	return {
		baseUrl: normalized,
		cardUrl: `${normalized}${AGENT_CARD_PATH}`,
		cardPath: AGENT_CARD_PATH,
		rpcUrl: `${normalized}/`
	};
}

export function formatProxyPath(agentId: string, path = ""): string {
	const cleanPath = path.replace(/^\/+/, "");
	return `/agents/${agentId}/${cleanPath}`;
}

export function buildEndpointConfig(options: ChatCliOptions): EndpointConfig {
	const direct = buildDirectEndpoints(options.url);
	if (!options.agentId) {
		return direct;
	}

	const cardPath = formatProxyPath(options.agentId, AGENT_CARD_PATH);
	return {
		baseUrl: direct.baseUrl,
		cardUrl: `${direct.baseUrl}${cardPath}`,
		cardPath,
		rpcUrl: `${direct.baseUrl}${formatProxyPath(options.agentId)}`
	};
}

function buildFetch(options: ChatCliOptions, endpoints: EndpointConfig): typeof fetch {
	return async (input, init) => {
		const isRequest = input instanceof Request;
		const originalRequest = isRequest ? input : undefined;
		const method = init?.method ?? originalRequest?.method ?? "GET";
		const targetUrl =
			options.agentId && method.toUpperCase() !== "GET"
				? endpoints.rpcUrl
				: isRequest
					? input.url
					: String(input);

		const headers = new Headers(init?.headers ?? originalRequest?.headers);
		for (const [key, value] of Object.entries(options.headers)) {
			headers.set(key, value);
		}
		if (options.token) {
			headers.set("Authorization", `Bearer ${options.token}`);
		}

		if (isRequest) {
			const request = originalRequest as Request;
			const body =
				init?.body ??
				(method.toUpperCase() === "GET" || method.toUpperCase() === "HEAD"
					? undefined
					: (request.body ?? undefined));
			const nextRequest: RequestInit & { duplex?: "half" } = {
				method,
				headers,
				body
			};
			if (body !== undefined) {
				nextRequest.duplex = "half";
			}

			return fetch(
				new Request(targetUrl, nextRequest)
			);
		}

		return fetch(targetUrl, {
			...init,
			method,
			headers
		});
	};
}

function rewriteAgentCard(agentCard: AgentCard, endpoints: EndpointConfig): AgentCard {
	return {
		...agentCard,
		url: endpoints.rpcUrl,
		additionalInterfaces:
			agentCard.additionalInterfaces?.map((item) => ({
				...item,
				url: endpoints.rpcUrl
			})) ?? agentCard.additionalInterfaces
	};
}

export async function connectClient(options: ChatCliOptions): Promise<ConnectedClient> {
	const endpoints = buildEndpointConfig(options);
	const fetchImpl = buildFetch(options, endpoints);
	const resolver = new DefaultAgentCardResolver({ fetchImpl });
	const resolvedCard = await resolver.resolve(endpoints.baseUrl, endpoints.cardPath);
	const agentCard = options.agentId
		? rewriteAgentCard(resolvedCard, endpoints)
		: resolvedCard;

	const factoryOptions = ClientFactoryOptions.createFrom(ClientFactoryOptions.default, {
		transports: [new JsonRpcTransportFactory({ fetchImpl })],
		preferredTransports: ["JSONRPC"],
		clientConfig: {
			acceptedOutputModes: ["text"]
		}
	});
	const factory = new ClientFactory(factoryOptions);
	const client = await factory.createFromAgentCard(agentCard);

	return {
		agentCard,
		client,
		endpoints
	};
}

export function createPushNotificationConfig(receiverUrl: string): PushNotificationConfig {
	const parsed = new URL(receiverUrl);
	return {
		id: randomUUID(),
		url: `${parsed.origin}/notify`,
		authentication: {
			schemes: ["bearer"]
		}
	};
}

/**
 * Builds an A2A `MessageSendParams` from pre-parsed parts.
 * Parts are produced by `buildMessageParts` and may include text, files, or other A2A part types.
 */
export function buildMessageParams(
	parts: Part[],
	contextId: string | undefined,
	taskId: string | undefined,
	pushNotificationConfig?: PushNotificationConfig
): MessageSendParams {
	return {
		message: {
			kind: "message",
			messageId: randomUUID(),
			role: "user",
			taskId,
			contextId,
			parts
		},
		metadata: generateTaskMetadata(),
		configuration: {
			acceptedOutputModes: ["text"],
			...(pushNotificationConfig ? { pushNotificationConfig } : {})
		}
	};
}
