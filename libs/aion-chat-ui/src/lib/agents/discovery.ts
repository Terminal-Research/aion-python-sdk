import type { AgentCard } from "@a2a-js/sdk";

import {
	type AionEnvironmentId,
	getGraphQLHttpUrlForBaseUrl
} from "../environment.js";
import { fetchRegistryAgentIdentities } from "../graphql/registry.js";
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

export interface AgentDiscoveryOptions {
	environmentId: AionEnvironmentId;
	controlPlaneAccessTokenProvider?: () => Promise<string | undefined>;
	graphQLFetchImpl?: typeof fetch;
	sourceFetchImpl?: (source: RuntimeAgentSource) => typeof fetch;
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

async function fetchJson<T>(
	url: string,
	fetchImpl: typeof fetch
): Promise<T | undefined> {
	const response = await fetchImpl(url);
	if (!response.ok) {
		return undefined;
	}
	return (await response.json()) as T;
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
	now: string
): Promise<SourceDiscoveryResult> {
	const manifestUrl = getManifestUrl(source.url);
	const manifest = await fetchJson<DiscoveryManifest>(manifestUrl, fetchImpl);
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
		const agentCard = await fetchJson<AgentCard>(agentCardUrl, fetchImpl);
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
	now: string
): Promise<SourceDiscoveryResult> {
	const agentCardUrl = getAgentCardUrl(source.url);
	const agentCard = await fetchJson<AgentCard>(agentCardUrl, fetchImpl);
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

async function discoverRegistrySource(
	source: RuntimeAgentSource,
	fetchImpl: typeof fetch,
	now: string,
	options: AgentDiscoveryOptions | undefined
): Promise<SourceDiscoveryResult> {
	const accessToken = await options?.controlPlaneAccessTokenProvider?.();
	if (!accessToken || !options) {
		return {
			source: {
				...source,
				type: "registry",
				status: "unavailable",
				lastCheckedAt: now,
				lastError: "Login required to load Aion registry agent sources."
			},
			agents: []
		};
	}

	const identities = await fetchRegistryAgentIdentities({
		environmentId: options.environmentId,
		accessToken,
		graphQLUrl: getGraphQLHttpUrlForBaseUrl(source.url),
		fetchImpl: options.graphQLFetchImpl ?? fetch
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
		const agentCard = await fetchJson<AgentCard>(agentCardUrl, fetchImpl);
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
			return await discoverManifestSource(source, fetchImpl, now);
		} catch {
			return discoverAgentCardSource(source, fetchImpl, now);
		}
	}

	if (source.type === "manifest") {
		return discoverManifestSource(source, fetchImpl, now);
	}
	if (source.type === "agentCard") {
		return discoverAgentCardSource(source, fetchImpl, now);
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
		try {
			const sourceFetchImpl = options?.sourceFetchImpl?.(source) ?? fetchImpl;
			const result = await discoverSource(source, sourceFetchImpl, now, options);
			resolvedSources.push(result.source);
			agents.push(...result.agents);
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			const failedSource: AgentSourceRecord = {
				...source,
				status: "unavailable",
				lastCheckedAt: now,
				lastError: message
			};
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
