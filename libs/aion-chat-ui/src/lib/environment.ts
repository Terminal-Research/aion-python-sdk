export const AION_ENVIRONMENT_IDS = [
	"production",
	"staging",
	"development"
] as const;

export type AionEnvironmentId = (typeof AION_ENVIRONMENT_IDS)[number];

export interface AionEnvironment {
	id: AionEnvironmentId;
	controlPlaneApiBaseUrl: string;
	webAppBaseUrl: string;
}

export const DEFAULT_AION_ENVIRONMENT_ID: AionEnvironmentId = "production";

export const AION_ENVIRONMENTS: Record<AionEnvironmentId, AionEnvironment> = {
	production: {
		id: "production",
		controlPlaneApiBaseUrl: "https://api.aion.to",
		webAppBaseUrl: "https://app.aion.to"
	},
	staging: {
		id: "staging",
		controlPlaneApiBaseUrl: "https://api-staging.aion.to",
		webAppBaseUrl: "https://app-staging.aion.to"
	},
	development: {
		id: "development",
		controlPlaneApiBaseUrl: "http://localhost:8080",
		webAppBaseUrl: "https://localhost:3000"
	}
};

export function isAionEnvironmentId(
	value: string | undefined
): value is AionEnvironmentId {
	return AION_ENVIRONMENT_IDS.includes(value as AionEnvironmentId);
}

export function normalizeEnvironmentId(
	value: string | undefined
): AionEnvironmentId {
	return isAionEnvironmentId(value) ? value : DEFAULT_AION_ENVIRONMENT_ID;
}

export function getAionEnvironment(id: AionEnvironmentId): AionEnvironment {
	return AION_ENVIRONMENTS[id];
}

export function getControlPlaneApiBaseUrl(id: AionEnvironmentId): string {
	return getAionEnvironment(id).controlPlaneApiBaseUrl;
}

export function getWebAppBaseUrl(id: AionEnvironmentId): string {
	return getAionEnvironment(id).webAppBaseUrl;
}

export function getAuthConfigUrl(id: AionEnvironmentId): string {
	return `${getControlPlaneApiBaseUrl(id)}/auth/cli/config`;
}

export function getGraphQLHttpUrlForBaseUrl(baseUrl: string): string {
	const parsed = new URL(baseUrl);
	parsed.pathname = "/api/graphql";
	parsed.search = "";
	parsed.hash = "";
	return parsed.toString();
}

export function getGraphQLHttpUrl(id: AionEnvironmentId): string {
	return getGraphQLHttpUrlForBaseUrl(getControlPlaneApiBaseUrl(id));
}

export function getGraphQLWebSocketUrl(id: AionEnvironmentId): string {
	const apiBaseUrl = getControlPlaneApiBaseUrl(id);
	const parsed = new URL(apiBaseUrl);
	parsed.protocol = parsed.protocol === "https:" ? "wss:" : "ws:";
	parsed.pathname = "/ws/graphql";
	parsed.search = "";
	parsed.hash = "";
	return parsed.toString();
}
