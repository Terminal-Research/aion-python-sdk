import { createHash } from "node:crypto";

import type { AgentCard } from "@a2a-js/sdk";

import type { AionEnvironmentId } from "../environment.js";

export type AgentSourceType = "manifest" | "agentCard" | "registry";
export type AgentSourceStatus = "unchecked" | "available" | "unavailable";

export interface AgentSourceRecord {
	sourceKey: string;
	type: AgentSourceType;
	url: string;
	description: string;
	enabled: boolean;
	isDefault?: boolean;
	status?: AgentSourceStatus;
	lastCheckedAt?: string;
	lastError?: string;
}

export interface RuntimeAgentSource extends AgentSourceRecord {
	resolveMode?: "auto";
	transient?: boolean;
}

export interface AgentRecord {
	agentKey: string;
	agentId?: string;
	sourceKey: string;
	agentCardUrl: string;
	agentCardName?: string;
	agentHandle?: string;
	lastSeenAt: string;
	lastLoadedAt?: string;
	status?: "available" | "unavailable";
	activeContextId?: string;
}

export interface DiscoveredAgentRecord extends AgentRecord {
	id: string;
	path: string;
	source: AgentSourceRecord;
	connectionUrl: string;
	connectionAgentId?: string;
	agentCard?: AgentCard;
}

export interface AgentContextSessionFile {
	schemaVersion: 1;
	environment: AionEnvironmentId;
	agentKey: string;
	contextId: string;
	createdAt: string;
	lastUpdatedAt: string;
	localTurnCount: number;
	lastTaskId?: string;
	summary?: string;
	messages: import("@a2a-js/sdk").Message[];
}

export const DEFAULT_LOCAL_AGENT_SOURCE_KEY = "default-localhost-8000";
export const DEFAULT_LOCAL_AGENT_SOURCE_URL = "http://localhost:8000";

export function hashValue(value: string): string {
	return createHash("sha256").update(value).digest("hex").slice(0, 12);
}

export function slugKey(value: string): string {
	const slug = value
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-+|-+$/g, "")
		.slice(0, 48);
	return slug || hashValue(value);
}

export function normalizeSourceUrl(url: string): string {
	const parsed = new URL(url);
	if (parsed.pathname !== "/" && parsed.pathname.endsWith("/")) {
		parsed.pathname = parsed.pathname.replace(/\/+$/u, "");
	}
	parsed.hash = "";
	return parsed.toString().replace(/\/$/u, "");
}

export function createDefaultLocalAgentSource(): AgentSourceRecord {
	return {
		sourceKey: DEFAULT_LOCAL_AGENT_SOURCE_KEY,
		type: "manifest",
		url: DEFAULT_LOCAL_AGENT_SOURCE_URL,
		description: "Local Aion SDK server",
		enabled: true,
		isDefault: true,
		status: "unchecked"
	};
}

export function createExplicitAgentSource(url: string): RuntimeAgentSource {
	const normalizedUrl = normalizeSourceUrl(url);
	return {
		sourceKey: `cli-${hashValue(normalizedUrl)}`,
		type: "manifest",
		url: normalizedUrl,
		description: "Provided with --url",
		enabled: true,
		resolveMode: "auto",
		transient: true,
		status: "unchecked"
	};
}

export function isTransientAgentSource(
	source: Pick<AgentSourceRecord, "sourceKey"> | RuntimeAgentSource
): boolean {
	return "transient" in source && source.transient === true;
}

export function createAgentKey(sourceKey: string, identifier: string): string {
	return `${sourceKey}:${slugKey(identifier)}-${hashValue(identifier).slice(0, 8)}`;
}

export function mergeAgentSources(
	persistedSources: Record<string, AgentSourceRecord>,
	explicitUrl?: string
): RuntimeAgentSource[] {
	const sources = new Map<string, RuntimeAgentSource>();
	const defaultSource = createDefaultLocalAgentSource();
	sources.set(defaultSource.sourceKey, defaultSource);

	for (const source of Object.values(persistedSources)) {
		if (source.enabled) {
			sources.set(source.sourceKey, source);
		}
	}

	if (explicitUrl) {
		const explicitSource = createExplicitAgentSource(explicitUrl);
		sources.set(explicitSource.sourceKey, explicitSource);
	}

	return [...sources.values()];
}
