/** Internal type. DO NOT USE DIRECTLY. */
type Exact<T extends { [key: string]: unknown }> = { [K in keyof T]: T[K] };
/** Internal type. DO NOT USE DIRECTLY. */
export type Incremental<T> = T | { [P in keyof T]?: P extends ' $fragmentName' | '__typename' ? T[P] : never };
/**
 * Classifies the role of the identity.
 *
 * - `Personal`: personal identity for an individual user and their profile/presence
 * - `Principal`: externally reachable non-daemon actor, such as a distribution-facing identity
 * - `Daemon`: internal project node daemon identity used for daemon addressing and capability-scoped permissions
 * - `System`: platform-managed identity reserved for internal system actors
 */
export type AgentIdentityType =
  | 'Daemon'
  | 'Personal'
  | 'Principal'
  | 'System';

/** Enumeration of supported network types. */
export type NetworkTypeGQL =
  | 'A2A'
  | 'Aion'
  | 'GitHub'
  | 'Playground'
  | 'Telegram'
  | 'Twitter';

export type Route =
  | 'Onboarding';

export type LoginBootstrapQueryVariables = Exact<{
  token: string;
}>;


export type LoginBootstrapQuery = { login: { nextRoute: Route | null, email: string | null, name: string | null } | null };

export type CurrentUserQueryVariables = Exact<{ [key: string]: never; }>;


export type CurrentUserQuery = { user: { id: string, homeOrganization: { id: string, name: string } | null, agentIdentity: { id: string, name: string | null, atName: string | null, a2aUrl: string | null, agentType: AgentIdentityType, organizationId: string, updatedAt: string } } | null };

export type AgentCatalogIdentitiesQueryVariables = Exact<{
  organizationId: string | number;
  networkTypes?: Array<NetworkTypeGQL> | NetworkTypeGQL | null | undefined;
}>;


export type AgentCatalogIdentitiesQuery = { agentIdentityDetails: Array<{ identity: { id: string, agentType: AgentIdentityType, userId: string | null, organizationId: string, systemKey: string | null, name: string | null, a2aUrl: string | null, website: string | null, email: string | null, atName: string | null, biography: string | null, avatarImageUrl: string | null, backgroundImageUrl: string | null, updatedAt: string, notes: string | null }, distributionUsages: Array<{ distributionId: string, networkType: NetworkTypeGQL }> }> | null };
