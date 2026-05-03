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

export async function fetchRegistryAgentIdentities(options: {
	environmentId: AionEnvironmentId;
	accessToken: string;
	fetchImpl?: typeof fetch;
	graphQLUrl?: string;
}): Promise<RegistryAgentIdentity[]> {
	const graphQLUrl = options.graphQLUrl ?? getGraphQLHttpUrl(options.environmentId);
	await runLoginBootstrap({
		environmentId: options.environmentId,
		accessToken: options.accessToken,
		fetchImpl: options.fetchImpl,
		graphQLUrl
	});

	const currentUser = await executeGraphQL<
		CurrentUserQuery,
		CurrentUserQueryVariables
	>({
		environmentId: options.environmentId,
		url: graphQLUrl,
		query: CURRENT_USER_QUERY,
		variables: {} as CurrentUserQueryVariables,
		accessToken: options.accessToken,
		fetchImpl: options.fetchImpl
	});

	const user = currentUser.data?.user;
	if (!user) {
		throw new Error("Aion registry did not return the current user.");
	}

	const organizationId = user.homeOrganization?.id ?? user.agentIdentity.organizationId;
	const catalog = await executeGraphQL<
		AgentCatalogIdentitiesQuery,
		AgentCatalogIdentitiesQueryVariables
	>({
		environmentId: options.environmentId,
		url: graphQLUrl,
		query: AGENT_CATALOG_IDENTITIES_QUERY,
		variables: {
			organizationId,
			networkTypes: ["A2A"]
		},
		accessToken: options.accessToken,
		fetchImpl: options.fetchImpl
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

	return [...identities.values()].sort((left, right) =>
		(left.atName ?? left.name ?? left.id).localeCompare(
			right.atName ?? right.name ?? right.id
		)
	);
}
