import { getControlPlaneApiBaseUrl, type AionEnvironmentId } from "./environment.js";
import type { RegistryAccount, RegistryWorkspace } from "./graphql/registry.js";
import { createAgentKey, hashValue, type AgentRecord } from "./agents/model.js";

export interface RegistryAccountPreference {
	workspaceId?: string;
	selectedAgentKeys?: Record<string, string>;
}

export class WorkspaceSelectionError extends Error {
	constructor(message: string, readonly account: RegistryAccount) {
		super(message);
		this.name = "WorkspaceSelectionError";
	}
}

export function formatWorkspaces(workspaces: RegistryWorkspace[]): string {
	return workspaces.map((workspace) => `${workspace.name} (${workspace.id})`).join(", ");
}

/** Resolve only against memberships fetched for this account, never cached labels. */
export function resolveWorkspace(
	account: RegistryAccount,
	options: { selector?: string; savedWorkspaceId?: string } = {}
): RegistryWorkspace {
	if (options.selector !== undefined) {
		const selector = options.selector.trim();
		const exactId = account.workspaces.find((item) => item.id === selector);
		if (exactId) return exactId;
		const matches = account.workspaces.filter((item) => item.name.trim().toLowerCase() === selector.toLowerCase());
		if (selector && matches.length === 1) return matches[0];
		if (matches.length > 1) {
			throw new WorkspaceSelectionError(`Workspace '${selector}' is ambiguous. Use an ID: ${formatWorkspaces(matches)}.`, account);
		}
		throw new WorkspaceSelectionError(`Workspace '${selector}' is unavailable to this account. Available workspaces: ${formatWorkspaces(account.workspaces) || "none"}.`, account);
	}
	const id = options.savedWorkspaceId ?? account.homeWorkspaceId;
	const workspace = account.workspaces.find((item) => item.id === id);
	if (workspace) return workspace;
	throw new WorkspaceSelectionError(
		options.savedWorkspaceId !== undefined
			? "Your saved workspace is no longer available. Use /workspace or --workspace to select another."
			: "No home workspace is available. Use /workspace or --workspace to select a workspace.",
		account
	);
}

/** Local namespaces are not credentials or server-side authorization claims. */
export function registryAccountScope(environment: AionEnvironmentId, userId: string, origin?: string): string {
	return hashValue(JSON.stringify([environment, getControlPlaneApiBaseUrl(environment, origin), userId]));
}

export function registryAgentKey(sourceKey: string, identityId: string, accountScope: string, workspaceId: string): string {
	return createAgentKey(`${sourceKey}:workspace-${hashValue(JSON.stringify([accountScope, workspaceId]))}`, identityId);
}

/** Only a credential-verified legacy home context can be adopted into a new key. */
export function eligibleLegacyRegistryAgent(options: {
	agents: Record<string, AgentRecord>;
	sourceKey: string;
	identityId: string;
	workspaceId: string;
	homeWorkspaceId?: string;
	credentialScope?: string;
}): AgentRecord | undefined {
	if (!options.credentialScope || options.workspaceId !== options.homeWorkspaceId) return undefined;
	const legacy = options.agents[createAgentKey(options.sourceKey, options.identityId)];
	return legacy?.sourceKey === options.sourceKey && legacy.agentId === options.identityId &&
		legacy.credentialScope === options.credentialScope ? legacy : undefined;
}

export function normalizeRegistryPreferences(value: unknown): Record<string, RegistryAccountPreference> | undefined {
	if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
	const entries = Object.entries(value).flatMap(([scope, raw]) => {
		if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
		const record = raw as Record<string, unknown>;
		const selectedAgentKeys = record.selectedAgentKeys && typeof record.selectedAgentKeys === "object" && !Array.isArray(record.selectedAgentKeys)
			? Object.fromEntries(Object.entries(record.selectedAgentKeys).filter((item): item is [string, string] => typeof item[1] === "string" && Boolean(item[1].trim())))
			: undefined;
		return [[scope, {
			// Preserve invalid explicit saved choices as errors instead of falling back home.
			...(record.workspaceId !== undefined ? { workspaceId: typeof record.workspaceId === "string" ? record.workspaceId : "" } : {}),
			...(selectedAgentKeys ? { selectedAgentKeys } : {})
		}]];
	});
	return Object.fromEntries(entries);
}
