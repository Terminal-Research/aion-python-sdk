import type { AgentCard } from "@a2a-js/sdk";

import {
	type AionEnvironmentId,
	getGraphQLHttpUrlForBaseUrl
} from "../environment.js";
import { fetchRegistryAgentIdentities } from "../graphql/registry.js";
import type { ChatSessionLogger } from "../sessionLogger.js";
import {
	createAgentKey,
	slugKey,
	type AgentRecord,
	type AgentSourceRecord,
	type DiscoveredAgentRecord,
	type RuntimeAgentSource
} from "./model.js";

const MANIFEST_PATH = "/.well-known/manifest.json";
const AGENT_CARD_PATH = "/.well-known/agent-card.json";
const AGENT_PROXY_PREFIX = "/agents/";
const REGISTRY_LOGIN_REQUIRED_MESSAGE = "/login to authenticate.";
const REGISTRY_AUTH_FAILED_MESSAGE = "Auth failed.";
const REGISTRY_CONTROL_PLANE_UNAVAILABLE_MESSAGE = "Aion Control Plane did not respond.";
const REGISTRY_UNEXPECTED_ERROR_MESSAGE = "Unexpected error.";

interface DiscoveryManifest {
	api_version?: string;
	name?: string;
	endpoints?: Record<string, string>;
}

export interface SourceDiscoveryResult {
	source: AgentSourceRecord;
	agents: DiscoveredAgentRecord[];
	error?: string;
}

export interface AgentDiscoveryResult {
	sources: AgentSourceRecord[];
	agents: DiscoveredAgentRecord[];
	errors: SourceDiscoveryResult[];
}

export interface DiscoveredAgentSelectionOptions {
	requestedAgentId?: string;
	selectedAgentKey?: string;
	selectedAgentId?: string;
	explicitSourceKey?: string;
	autoSelectExplicit?: boolean;
}

export interface ControlPlaneAccessTokenRequest {
	forceRefresh?: boolean;
}

export interface AgentDiscoveryOptions {
	environmentId: AionEnvironmentId;
	controlPlaneAccessTokenProvider?: (
		request?: ControlPlaneAccessTokenRequest
	) => Promise<string | undefined>;
	graphQLFetchImpl?: typeof fetch;
	sourceFetchImpl?: (source: RuntimeAgentSource) => typeof fetch;
	logger?: ChatSessionLogger;
}

function trimTrailingSlash(value: string): string {
	return value.endsWith("/") ? value.slice(0, -1) : value;
}

function normalizeBase(url: string): URL {
	const parsed = new URL(url);
	if (!parsed.pathname || parsed.pathname === "/") {
		parsed.pathname = "/";
		return parsed;
	}

	if (parsed.pathname.endsWith(MANIFEST_PATH)) {
		parsed.pathname = parsed.pathname.slice(0, -MANIFEST_PATH.length) || "/";
		return parsed;
	}

	if (parsed.pathname.endsWith(AGENT_CARD_PATH)) {
		parsed.pathname = parsed.pathname.slice(0, -AGENT_CARD_PATH.length) || "/";
		return parsed;
	}

	const proxyIndex = parsed.pathname.indexOf(AGENT_PROXY_PREFIX);
	if (proxyIndex >= 0) {
		parsed.pathname = parsed.pathname.slice(0, proxyIndex) || "/";
		return parsed;
	}

	return parsed;
}

function getManifestUrl(url: string): string {
	if (new URL(url).pathname.endsWith(MANIFEST_PATH)) {
		return url;
	}
	return new URL(MANIFEST_PATH, normalizeBase(url)).toString();
}

function getAgentCardUrl(url: string): string {
	if (new URL(url).pathname.endsWith(AGENT_CARD_PATH)) {
		return url;
	}
	return new URL(AGENT_CARD_PATH, normalizeBase(url)).toString();
}

function resolveEndpoint(rootUrl: string, endpoint: string): string {
	return endpoint.startsWith("http")
		? trimTrailingSlash(endpoint)
		: trimTrailingSlash(new URL(endpoint, `${rootUrl}/`).toString());
}

function cardUrlForEndpoint(endpointUrl: string): string {
	return endpointUrl.endsWith(AGENT_CARD_PATH)
		? endpointUrl
		: `${trimTrailingSlash(endpointUrl)}${AGENT_CARD_PATH}`;
}

interface FetchJsonLogOptions {
	logger?: ChatSessionLogger;
	eventPrefix: string;
	context?: Record<string, unknown>;
}

function summarizeSourceForLog(
	source: RuntimeAgentSource | AgentSourceRecord
): Record<string, unknown> {
	return {
		sourceKey: source.sourceKey,
		type: source.type,
		url: source.url,
		description: source.description,
		enabled: source.enabled,
		status: source.status,
		lastError: source.lastError,
		isDefault: source.isDefault,
		resolveMode: "resolveMode" in source ? source.resolveMode : undefined,
		transient: "transient" in source ? source.transient : undefined
	};
}

function summarizeAgentForLog(agent: DiscoveredAgentRecord): Record<string, unknown> {
	return {
		agentKey: agent.agentKey,
		agentId: agent.agentId,
		id: agent.id,
		sourceKey: agent.sourceKey,
		agentCardUrl: agent.agentCardUrl,
		agentCardName: agent.agentCardName,
		agentHandle: agent.agentHandle,
		status: agent.status,
		connectionUrl: agent.connectionUrl
	};
}

async function fetchJson<T>(
	url: string,
	fetchImpl: typeof fetch,
	logOptions?: FetchJsonLogOptions
): Promise<T | undefined> {
	const startedAt = Date.now();
	logOptions?.logger?.debug(`${logOptions.eventPrefix}.request`, {
		url,
		...logOptions.context
	});

	let response: Response;
	try {
		response = await fetchImpl(url);
	} catch (error) {
		logOptions?.logger?.warn(`${logOptions.eventPrefix}.failed`, {
			url,
			durationMs: Date.now() - startedAt,
			error,
			...logOptions.context
		});
		throw error;
	}

	if (!response.ok) {
		logOptions?.logger?.warn(`${logOptions.eventPrefix}.http_error`, {
			url,
			status: response.status,
			statusText: response.statusText,
			durationMs: Date.now() - startedAt,
			...logOptions.context
		});
		return undefined;
	}

	try {
		const payload = (await response.json()) as T;
		logOptions?.logger?.debug(`${logOptions.eventPrefix}.response`, {
			url,
			status: response.status,
			durationMs: Date.now() - startedAt,
			...logOptions.context
		});
		return payload;
	} catch (error) {
		logOptions?.logger?.warn(`${logOptions.eventPrefix}.invalid_json`, {
			url,
			status: response.status,
			durationMs: Date.now() - startedAt,
			error,
			...logOptions.context
		});
		throw error;
	}
}

function createDiscoveredAgent(
	source: AgentSourceRecord,
	agentId: string | undefined,
	path: string,
	agentCardUrl: string,
	connectionUrl: string,
	connectionAgentId: string | undefined,
	now: string,
	agentCard?: AgentCard
): DiscoveredAgentRecord {
	const identifier = agentId ?? agentCard?.name ?? agentCardUrl;
	const agentKey = createAgentKey(source.sourceKey, identifier);
	return {
		agentKey,
		...(agentId ? { agentId } : {}),
		id: agentId ?? agentCard?.name ?? agentKey,
		path,
		sourceKey: source.sourceKey,
		source,
		agentCardUrl,
		agentCardName: agentCard?.name,
		lastSeenAt: now,
		...(agentCard ? { lastLoadedAt: now } : {}),
		status: agentCard ? "available" : "unavailable",
		connectionUrl,
		...(connectionAgentId ? { connectionAgentId } : {}),
		...(agentCard ? { agentCard } : {})
	};
}

async function discoverManifestSource(
	source: RuntimeAgentSource,
	fetchImpl: typeof fetch,
	now: string,
	logger?: ChatSessionLogger
): Promise<SourceDiscoveryResult> {
	const manifestUrl = getManifestUrl(source.url);
	const manifest = await fetchJson<DiscoveryManifest>(manifestUrl, fetchImpl, {
		logger,
		eventPrefix: "manifest",
		context: { sourceKey: source.sourceKey }
	});
	if (!manifest) {
		throw new Error(`Failed to fetch manifest from ${manifestUrl}`);
	}

	const rootUrl = trimTrailingSlash(normalizeBase(source.url).toString());
	const resolvedSource: AgentSourceRecord = {
		...source,
		type: "manifest",
		url: rootUrl,
		status: "available",
		lastCheckedAt: now,
		lastError: undefined
	};
	const agents: DiscoveredAgentRecord[] = [];

	for (const [agentId, endpoint] of Object.entries(manifest.endpoints ?? {})) {
		const endpointUrl = resolveEndpoint(rootUrl, endpoint);
		const agentCardUrl = cardUrlForEndpoint(endpointUrl);
		const agentCard = await fetchJson<AgentCard>(agentCardUrl, fetchImpl, {
			logger,
			eventPrefix: "agent_card",
			context: {
				sourceKey: source.sourceKey,
				agentId,
				endpointUrl
			}
		});
		agents.push(
			createDiscoveredAgent(
				resolvedSource,
				agentId,
				endpoint,
				agentCardUrl,
				rootUrl,
				agentId,
				now,
				agentCard
			)
		);
	}

	return { source: resolvedSource, agents };
}

async function discoverAgentCardSource(
	source: RuntimeAgentSource,
	fetchImpl: typeof fetch,
	now: string,
	logger?: ChatSessionLogger
): Promise<SourceDiscoveryResult> {
	const agentCardUrl = getAgentCardUrl(source.url);
	const agentCard = await fetchJson<AgentCard>(agentCardUrl, fetchImpl, {
		logger,
		eventPrefix: "agent_card",
		context: { sourceKey: source.sourceKey }
	});
	if (!agentCard) {
		throw new Error(`Failed to fetch agent card from ${agentCardUrl}`);
	}

	const resolvedSource: AgentSourceRecord = {
		...source,
		type: "agentCard",
		url: trimTrailingSlash(agentCardUrl),
		status: "available",
		lastCheckedAt: now,
		lastError: undefined
	};
	const connectionUrl = trimTrailingSlash(agentCardUrl);
	const agent = createDiscoveredAgent(
		resolvedSource,
		undefined,
		agentCardUrl,
		agentCardUrl,
		connectionUrl,
		undefined,
		now,
		agentCard
	);
	return { source: resolvedSource, agents: [agent] };
}

function normalizeAgentHandle(value: string | undefined): string | undefined {
	const trimmed = value?.trim();
	if (!trimmed) {
		return undefined;
	}
	return trimmed.startsWith("@") ? trimmed : `@${trimmed}`;
}

function identityDisplayId(identity: {
	id: string;
	name?: string;
	atName?: string;
}): string {
	const handle = normalizeAgentHandle(identity.atName);
	if (handle) {
		return handle.slice(1);
	}
	if (identity.name?.trim()) {
		return slugKey(identity.name);
	}
	return identity.id;
}

function registryUnavailableResult(
	source: RuntimeAgentSource,
	now: string,
	lastError: string
): SourceDiscoveryResult {
	return {
		source: {
			...source,
			type: "registry",
			status: "unavailable",
			lastCheckedAt: now,
			lastError
		},
		agents: [],
		error: lastError
	};
}

function registryDiscoveryErrorMessage(error: unknown): string {
	const message = error instanceof Error ? error.message : String(error);
	if (/workos|token|auth|login|credential|jwt|expired|unauthorized|forbidden/iu.test(message)) {
		return REGISTRY_AUTH_FAILED_MESSAGE;
	}
	if (
		error instanceof TypeError ||
		error instanceof SyntaxError ||
		/graphql|current user|fetch|network|http request|failed to fetch|response|json/iu.test(
			message
		)
	) {
		return REGISTRY_CONTROL_PLANE_UNAVAILABLE_MESSAGE;
	}
	return REGISTRY_UNEXPECTED_ERROR_MESSAGE;
}

function isRegistryAuthenticationError(error: unknown): boolean {
	const message = error instanceof Error ? error.message : String(error);
	return /workos|token|auth|login|credential|jwt|expired|unauthorized|forbidden|401|403/iu.test(
		message
	);
}

async function fetchRegistryIdentitiesWithToken(
	source: RuntimeAgentSource,
	accessToken: string,
	options: AgentDiscoveryOptions
): Promise<Awaited<ReturnType<typeof fetchRegistryAgentIdentities>>> {
	return fetchRegistryAgentIdentities({
		environmentId: options.environmentId,
		accessToken,
		graphQLUrl: getGraphQLHttpUrlForBaseUrl(source.url),
		fetchImpl: options.graphQLFetchImpl ?? fetch,
		logger: options.logger
	});
}

async function discoverRegistrySource(
	source: RuntimeAgentSource,
	fetchImpl: typeof fetch,
	now: string,
	options: AgentDiscoveryOptions | undefined
): Promise<SourceDiscoveryResult> {
	let accessToken: string | undefined;
	try {
		accessToken = await options?.controlPlaneAccessTokenProvider?.();
	} catch (error) {
		options?.logger?.warn("registry.auth.token_failed", {
			sourceKey: source.sourceKey,
			error
		});
		return registryUnavailableResult(source, now, REGISTRY_AUTH_FAILED_MESSAGE);
	}

	if (!accessToken || !options) {
		options?.logger?.warn("registry.auth.token_missing", {
			sourceKey: source.sourceKey
		});
		return registryUnavailableResult(source, now, REGISTRY_LOGIN_REQUIRED_MESSAGE);
	}

	let identities;
	try {
		options.logger?.debug("registry.identities.requested", {
			sourceKey: source.sourceKey,
			graphQLUrl: getGraphQLHttpUrlForBaseUrl(source.url)
		});
		identities = await fetchRegistryIdentitiesWithToken(
			source,
			accessToken,
			options
		);
	} catch (error) {
		if (isRegistryAuthenticationError(error)) {
			options.logger?.warn("registry.auth.failed", {
				sourceKey: source.sourceKey,
				error
			});
			try {
				options.logger?.debug("registry.auth.refresh_requested", {
					sourceKey: source.sourceKey
				});
				const refreshedAccessToken =
					await options.controlPlaneAccessTokenProvider?.({ forceRefresh: true });
				if (refreshedAccessToken && refreshedAccessToken !== accessToken) {
					identities = await fetchRegistryIdentitiesWithToken(
						source,
						refreshedAccessToken,
						options
					);
				}
			} catch (refreshError) {
				options.logger?.warn("registry.auth.refresh_failed", {
					sourceKey: source.sourceKey,
					error: refreshError
				});
				return registryUnavailableResult(
					source,
					now,
					registryDiscoveryErrorMessage(refreshError)
				);
			}
		}

		if (!identities) {
			options.logger?.warn("registry.identities.failed", {
				sourceKey: source.sourceKey,
				error
			});
			return registryUnavailableResult(
				source,
				now,
				registryDiscoveryErrorMessage(error)
			);
		}
	}
	options.logger?.debug("registry.identities.resolved", {
		sourceKey: source.sourceKey,
		identityCount: identities.length,
		identities: identities.map((identity) => ({
			id: identity.id,
			name: identity.name,
			atName: identity.atName,
			a2aUrl: identity.a2aUrl
		}))
	});
	const resolvedSource: AgentSourceRecord = {
		...source,
		type: "registry",
		status: "available",
		lastCheckedAt: now,
		lastError: undefined
	};
	const agents: DiscoveredAgentRecord[] = [];

	for (const identity of identities) {
		const agentCardUrl = cardUrlForEndpoint(identity.a2aUrl);
		const agentCard = await fetchJson<AgentCard>(agentCardUrl, fetchImpl, {
			logger: options.logger,
			eventPrefix: "agent_card",
			context: {
				sourceKey: source.sourceKey,
				identityId: identity.id,
				identityName: identity.name,
				identityAtName: identity.atName
			}
		});
		if (!agentCard) {
			continue;
		}

		const displayId = identityDisplayId(identity);
		agents.push({
			agentKey: createAgentKey(source.sourceKey, identity.id),
			agentId: identity.id,
			id: displayId,
			path: identity.a2aUrl,
			sourceKey: source.sourceKey,
			source: resolvedSource,
			agentCardUrl,
			agentCardName: agentCard.name ?? identity.name,
			agentHandle: normalizeAgentHandle(identity.atName),
			lastSeenAt: now,
			lastLoadedAt: now,
			status: "available",
			connectionUrl: agentCardUrl,
			agentCard
		});
	}

	return { source: resolvedSource, agents };
}

async function discoverSource(
	source: RuntimeAgentSource,
	fetchImpl: typeof fetch,
	now: string,
	options?: AgentDiscoveryOptions
): Promise<SourceDiscoveryResult> {
	if (!source.enabled) {
		return { source, agents: [] };
	}

	if (source.resolveMode === "auto") {
		try {
			return await discoverManifestSource(
				source,
				fetchImpl,
				now,
				options?.logger
			);
		} catch {
			return discoverAgentCardSource(
				source,
				fetchImpl,
				now,
				options?.logger
			);
		}
	}

	if (source.type === "manifest") {
		return discoverManifestSource(source, fetchImpl, now, options?.logger);
	}
	if (source.type === "agentCard") {
		return discoverAgentCardSource(source, fetchImpl, now, options?.logger);
	}
	if (source.type === "registry") {
		return discoverRegistrySource(source, fetchImpl, now, options);
	}

	throw new Error("Registry agent sources are not supported yet.");
}

export async function discoverAgentSources(
	sources: RuntimeAgentSource[],
	fetchImpl: typeof fetch = fetch,
	options?: AgentDiscoveryOptions
): Promise<AgentDiscoveryResult> {
	const now = new Date().toISOString();
	const resolvedSources: AgentSourceRecord[] = [];
	const agents: DiscoveredAgentRecord[] = [];
	const errors: SourceDiscoveryResult[] = [];

	for (const source of sources) {
		options?.logger?.debug("source.discovery.started", summarizeSourceForLog(source));
		try {
			const sourceFetchImpl = options?.sourceFetchImpl?.(source) ?? fetchImpl;
			const result = await discoverSource(source, sourceFetchImpl, now, options);
			resolvedSources.push(result.source);
			agents.push(...result.agents);
			if (result.error) {
				options?.logger?.warn("source.discovery.failed", {
					...summarizeSourceForLog(result.source),
					error: result.error
				});
				errors.push(result);
			} else {
				options?.logger?.debug("source.discovery.completed", {
					...summarizeSourceForLog(result.source),
					agentCount: result.agents.length,
					agents: result.agents.map(summarizeAgentForLog)
				});
			}
		} catch (error) {
			const message =
				source.type === "registry"
					? registryDiscoveryErrorMessage(error)
					: error instanceof Error
						? error.message
						: String(error);
			const failedSource: AgentSourceRecord = {
				...source,
				status: "unavailable",
				lastCheckedAt: now,
				lastError: message
			};
			options?.logger?.warn("source.discovery.failed", {
				...summarizeSourceForLog(failedSource),
				error,
				message
			});
			resolvedSources.push(failedSource);
			errors.push({
				source: failedSource,
				agents: [],
				error: message
			});
		}
	}

	const agentMap = new Map<string, DiscoveredAgentRecord>();
	for (const agent of agents) {
		agentMap.set(agent.agentKey, agent);
	}

	return {
		sources: resolvedSources,
		agents: [...agentMap.values()].sort((left, right) => left.id.localeCompare(right.id)),
		errors
	};
}

export function toPersistedAgents(
	agents: DiscoveredAgentRecord[],
	existingAgents: Record<string, AgentRecord>
): Record<string, AgentRecord> {
	const nextAgents: Record<string, AgentRecord> = { ...existingAgents };
	for (const agent of agents) {
		const current = nextAgents[agent.agentKey];
		nextAgents[agent.agentKey] = {
			...current,
			agentKey: agent.agentKey,
			agentId: agent.agentId,
			sourceKey: agent.sourceKey,
			agentCardUrl: agent.agentCardUrl,
			agentCardName: agent.agentCardName,
			agentHandle: agent.agentHandle ?? current?.agentHandle,
			lastSeenAt: agent.lastSeenAt,
			lastLoadedAt: agent.lastLoadedAt ?? current?.lastLoadedAt,
			status: agent.status,
			activeContextId: current?.activeContextId
		};
	}
	return nextAgents;
}

export function selectDiscoveredAgent(
	agents: DiscoveredAgentRecord[],
	options: DiscoveredAgentSelectionOptions
): DiscoveredAgentRecord | undefined {
	const explicitAgents = options.explicitSourceKey
		? agents.filter((agent) => agent.sourceKey === options.explicitSourceKey)
		: [];
	const findByRequestedId = (
		candidates: DiscoveredAgentRecord[]
	): DiscoveredAgentRecord | undefined =>
		candidates.find((agent) => agent.id === options.requestedAgentId);

	if (options.requestedAgentId) {
		return findByRequestedId(explicitAgents) ?? findByRequestedId(agents);
	}

	const selectedByKey = options.selectedAgentKey
		? agents.find((agent) => agent.agentKey === options.selectedAgentKey)
		: undefined;
	if (selectedByKey) {
		return selectedByKey;
	}

	const selectedByLegacyId = options.selectedAgentId
		? agents.find((agent) => agent.id === options.selectedAgentId)
		: undefined;
	if (selectedByLegacyId) {
		return selectedByLegacyId;
	}

	if (options.autoSelectExplicit) {
		if (explicitAgents.length === 1) {
			return explicitAgents[0];
		}
		if (agents.length === 1) {
			return agents[0];
		}
	}

	return undefined;
}
