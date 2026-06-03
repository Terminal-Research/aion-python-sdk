import { randomUUID } from "node:crypto";

import type {
	AgentCard,
	AgentInterface,
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

interface AgentCardSupportedInterface {
	url?: unknown;
	protocolBinding?: unknown;
	protocolVersion?: unknown;
	tenant?: unknown;
}

interface AgentCardWithSupportedInterfaces extends AgentCard {
	supportedInterfaces?: AgentCardSupportedInterface[];
}

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

export function normalizeAgentCardTransports(agentCard: AgentCard): AgentCard {
	const supportedInterfaces =
		(agentCard as AgentCardWithSupportedInterfaces).supportedInterfaces ?? [];
	const additionalInterfacesFromSupported = supportedInterfaces
		.map((item): AgentInterface | undefined => {
			if (typeof item.url !== "string" || typeof item.protocolBinding !== "string") {
				return undefined;
			}
			return {
				url: item.url,
				transport: item.protocolBinding
			};
		})
		.filter((item): item is AgentInterface => Boolean(item));

	if (additionalInterfacesFromSupported.length === 0) {
		return agentCard;
	}

	const preferredInterface =
		additionalInterfacesFromSupported.find((item) =>
			CLIENT_TRANSPORT_PREFERENCES.includes(
				item.transport as (typeof CLIENT_TRANSPORT_PREFERENCES)[number]
			)
		) ?? additionalInterfacesFromSupported[0];

	return {
		...agentCard,
		url: agentCard.url ?? preferredInterface.url,
		preferredTransport: agentCard.preferredTransport ?? preferredInterface.transport,
		additionalInterfaces: [
			...(agentCard.additionalInterfaces ?? []),
			...additionalInterfacesFromSupported
		]
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

export async function connectClient(options: ChatConnectionOptions): Promise<ConnectedClient> {
	const endpoints = buildEndpointConfig(options);
	const fetchImpl = buildFetch(options, endpoints);
	const resolver = new DefaultAgentCardResolver({ fetchImpl });
	const resolvedCard = await resolver.resolve(endpoints.baseUrl, endpoints.cardPath);
	const agentCard = normalizeAgentCardTransports(
		options.agentId ? rewriteAgentCard(resolvedCard, endpoints) : resolvedCard
	);

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
			acceptedOutputModes: [...ACCEPTED_OUTPUT_MODES],
			...(pushNotificationConfig ? { pushNotificationConfig } : {})
		}
	};
}
