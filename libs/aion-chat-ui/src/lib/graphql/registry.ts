import type {
	AgentCatalogIdentitiesQuery,
	AgentCatalogIdentitiesQueryVariables,
	CurrentUserQuery,
	CurrentUserQueryVariables
} from "../../graphql/generated/graphql.js";
import {
	type AionEnvironmentId,
	getGraphQLHttpUrl
} from "../environment.js";
import type { ChatSessionLogger } from "../sessionLogger.js";
import { runLoginBootstrap } from "./authBootstrap.js";
import { executeGraphQL } from "./client.js";

export interface RegistryAgentIdentity {
	id: string;
	name?: string;
	atName?: string;
	a2aUrl: string;
	updatedAt: string;
}

export const CURRENT_USER_QUERY = `
query CurrentUser {
	user {
		id
		homeOrganization {
			id
			name
		}
		agentIdentity {
			id
			name
			atName
			a2aUrl
			agentType
			organizationId
			updatedAt
		}
	}
}
`;

export const AGENT_CATALOG_IDENTITIES_QUERY = `
query AgentCatalogIdentities($organizationId: ID!, $networkTypes: [NetworkTypeGQL!]) {
	agentIdentityDetails(
		organizationId: $organizationId
		types: [Principal, Personal]
		networkTypes: $networkTypes
		includePersonalSelf: true
	) {
		identity {
			id
			agentType
			userId
			organizationId
			systemKey
			name
			a2aUrl
			website
			email
			atName
			biography
			avatarImageUrl
			backgroundImageUrl
			updatedAt
			notes
		}
		distributionUsages {
			distributionId
			networkType
		}
	}
}
`;

function normalizeIdentity(
	identity:
		| NonNullable<CurrentUserQuery["user"]>["agentIdentity"]
		| NonNullable<
				NonNullable<AgentCatalogIdentitiesQuery["agentIdentityDetails"]>[number]
		  >["identity"]
): RegistryAgentIdentity | undefined {
	const a2aUrl = identity.a2aUrl?.trim();
	if (!a2aUrl) {
		return undefined;
	}

	return {
		id: identity.id,
		...(identity.name?.trim() ? { name: identity.name.trim() } : {}),
		...(identity.atName?.trim() ? { atName: identity.atName.trim() } : {}),
		a2aUrl,
		updatedAt: identity.updatedAt
	};
}

function summarizeRegistryIdentity(
	identity: RegistryAgentIdentity
): Record<string, unknown> {
	return {
		id: identity.id,
		name: identity.name,
		atName: identity.atName,
		a2aUrl: identity.a2aUrl,
		updatedAt: identity.updatedAt
	};
}

function summarizeCurrentUser(
	user: NonNullable<CurrentUserQuery["user"]>
): Record<string, unknown> {
	return {
		userId: user.id,
		homeOrganizationId: user.homeOrganization?.id,
		organizationId: user.agentIdentity.organizationId,
		agentIdentityId: user.agentIdentity.id,
		agentIdentityName: user.agentIdentity.name,
		agentIdentityAtName: user.agentIdentity.atName,
		agentIdentityType: user.agentIdentity.agentType,
		agentIdentityHasA2aUrl: Boolean(user.agentIdentity.a2aUrl)
	};
}

export async function fetchRegistryAgentIdentities(options: {
	environmentId: AionEnvironmentId;
	accessToken: string;
	fetchImpl?: typeof fetch;
	graphQLUrl?: string;
	logger?: ChatSessionLogger;
}): Promise<RegistryAgentIdentity[]> {
	const graphQLUrl = options.graphQLUrl ?? getGraphQLHttpUrl(options.environmentId);
	const bootstrap = await runLoginBootstrap({
		environmentId: options.environmentId,
		accessToken: options.accessToken,
		fetchImpl: options.fetchImpl,
		graphQLUrl,
		logger: options.logger
	});
	options.logger?.debug("registry.login_bootstrap.loaded", {
		nextRoutePath: bootstrap.nextRoutePath,
		hasLoginEmail: Boolean(bootstrap.loginEmail),
		hasLoginName: Boolean(bootstrap.loginName)
	});

	const currentUser = await executeGraphQL<
		CurrentUserQuery,
		CurrentUserQueryVariables
	>({
		environmentId: options.environmentId,
		url: graphQLUrl,
		operationName: "CurrentUser",
		query: CURRENT_USER_QUERY,
		variables: {} as CurrentUserQueryVariables,
		accessToken: options.accessToken,
		fetchImpl: options.fetchImpl,
		logger: options.logger
	});

	const user = currentUser.data?.user;
	if (!user) {
		throw new Error("Aion registry did not return the current user.");
	}

	options.logger?.debug("registry.current_user.loaded", summarizeCurrentUser(user));

	const organizationId = user.homeOrganization?.id ?? user.agentIdentity.organizationId;
	const catalog = await executeGraphQL<
		AgentCatalogIdentitiesQuery,
		AgentCatalogIdentitiesQueryVariables
	>({
		environmentId: options.environmentId,
		url: graphQLUrl,
		operationName: "AgentCatalogIdentities",
		query: AGENT_CATALOG_IDENTITIES_QUERY,
		variables: {
			organizationId,
			networkTypes: ["A2A"]
		},
		accessToken: options.accessToken,
		fetchImpl: options.fetchImpl,
		logger: options.logger
	});

	const identities = new Map<string, RegistryAgentIdentity>();
	const personalIdentity = normalizeIdentity(user.agentIdentity);
	if (personalIdentity) {
		identities.set(personalIdentity.id, personalIdentity);
	}

	for (const detail of catalog.data?.agentIdentityDetails ?? []) {
		const identity = normalizeIdentity(detail.identity);
		if (identity) {
			identities.set(identity.id, identity);
		}
	}

	const resolvedIdentities = [...identities.values()].sort((left, right) =>
		(left.atName ?? left.name ?? left.id).localeCompare(
			right.atName ?? right.name ?? right.id
		)
	);
	options.logger?.debug("registry.agent_identities.loaded", {
		organizationId,
		catalogIdentityCount: catalog.data?.agentIdentityDetails?.length ?? 0,
		resolvedIdentityCount: resolvedIdentities.length,
		identities: resolvedIdentities.map(summarizeRegistryIdentity)
	});

	return resolvedIdentities;
}
