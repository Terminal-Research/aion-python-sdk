import { randomUUID } from "node:crypto";

import type {
	AgentCard,
	Message,
	Part,
	SendMessageRequest,
	Task,
	TaskArtifactUpdateEvent,
	TaskPushNotificationConfig,
	TaskStatusUpdateEvent
} from "@a2a-js/sdk";
import { Role } from "@a2a-js/sdk";
import {
	ClientFactory,
	ClientFactoryOptions,
	DefaultAgentCardResolver,
	JsonRpcTransportFactory,
	RestTransportFactory,
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
export type TokenProvider = () => Promise<string | undefined>;

export interface AuthenticatedFetchOptions {
	token?: string;
	tokenProvider?: TokenProvider;
	headers: Record<string, string>;
}

export interface ChatConnectionOptions extends ChatCliOptions {
	url: string;
	tokenProvider?: TokenProvider;
}

const AGENT_CARD_PATH = "/.well-known/agent-card.json";
const CLIENT_TRANSPORT_PREFERENCES = ["JSONRPC", "HTTP+JSON"] as const;
const ACCEPTED_OUTPUT_MODES = ["text", "text/plain", "application/json"] as const;

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

export function buildEndpointConfig(options: ChatConnectionOptions): EndpointConfig {
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

async function buildAuthHeaders(
	options: AuthenticatedFetchOptions,
	initHeaders: HeadersInit | undefined,
	requestHeaders: HeadersInit | undefined
): Promise<Headers> {
	const headers = new Headers(initHeaders ?? requestHeaders);
	for (const [key, value] of Object.entries(options.headers)) {
		headers.set(key, value);
	}
	const token = options.token ?? (await options.tokenProvider?.());
	if (token) {
		headers.set("Authorization", `Bearer ${token}`);
	}
	return headers;
}

export function buildAuthenticatedFetch(
	options: AuthenticatedFetchOptions
): typeof fetch {
	return async (input, init) => {
		const isRequest = input instanceof Request;
		const originalRequest = isRequest ? input : undefined;
		const method = init?.method ?? originalRequest?.method ?? "GET";
		const headers = await buildAuthHeaders(
			options,
			init?.headers,
			originalRequest?.headers
		);

		if (isRequest) {
			const request = originalRequest as Request;
			const body =
				init?.body ??
				(method.toUpperCase() === "GET" || method.toUpperCase() === "HEAD"
					? undefined
					: (request.body ?? undefined));
			const nextRequest: RequestInit & { duplex?: "half" } = {
				...init,
				method,
				headers,
				body
			};
			if (body !== undefined) {
				nextRequest.duplex = "half";
			}

			return fetch(new Request(request, nextRequest));
		}

		return fetch(input, {
			...init,
			method,
			headers
		});
	};
}

function buildFetch(options: ChatConnectionOptions, endpoints: EndpointConfig): typeof fetch {
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

		const headers = await buildAuthHeaders(
			options,
			init?.headers,
			originalRequest?.headers
		);

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
		supportedInterfaces: agentCard.supportedInterfaces.map((item) => ({
			...item,
			url: endpoints.rpcUrl
		}))
	};
}

export async function connectClient(options: ChatConnectionOptions): Promise<ConnectedClient> {
	const endpoints = buildEndpointConfig(options);
	const fetchImpl = buildFetch(options, endpoints);
	const resolver = new DefaultAgentCardResolver({ fetchImpl });
	const resolvedCard = await resolver.resolve(endpoints.baseUrl, endpoints.cardPath);
	const agentCard = options.agentId
		? rewriteAgentCard(resolvedCard, endpoints)
		: resolvedCard;

	const factoryOptions = ClientFactoryOptions.createFrom(ClientFactoryOptions.default, {
		transports: [
			new JsonRpcTransportFactory({ fetchImpl }),
			new RestTransportFactory({ fetchImpl })
		],
		preferredTransports: [...CLIENT_TRANSPORT_PREFERENCES],
		clientConfig: {
			acceptedOutputModes: [...ACCEPTED_OUTPUT_MODES]
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

export function createPushNotificationConfig(receiverUrl: string): TaskPushNotificationConfig {
	const parsed = new URL(receiverUrl);
	return {
		tenant: "",
		id: randomUUID(),
		taskId: "",
		url: `${parsed.origin}/notify`,
		token: randomUUID(),
		authentication: {
			scheme: "bearer",
			credentials: ""
		}
	};
}

/**
 * Builds an A2A `SendMessageRequest` from pre-parsed parts.
 * Parts are produced by `buildMessageParts` and may include text, files, or other A2A part types.
 */
export function buildMessageParams(
	parts: Part[],
	contextId: string | undefined,
	taskId: string | undefined,
	pushNotificationConfig?: TaskPushNotificationConfig
): SendMessageRequest {
	return {
		tenant: "",
		message: {
			messageId: randomUUID(),
			role: Role.ROLE_USER,
			taskId: taskId ?? "",
			contextId: contextId ?? "",
			parts,
			metadata: undefined,
			extensions: [],
			referenceTaskIds: []
		},
		metadata: generateTaskMetadata(),
		configuration: {
			acceptedOutputModes: [...ACCEPTED_OUTPUT_MODES],
			taskPushNotificationConfig: pushNotificationConfig,
			historyLength: undefined,
			returnImmediately: false
		}
	};
}
