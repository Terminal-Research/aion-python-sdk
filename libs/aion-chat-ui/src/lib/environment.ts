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

/** Explicit development API origin; never inferred from the agent's environment. */
export function normalizeControlPlaneOrigin(
	id: AionEnvironmentId, value?: string
): string | undefined {
	if (value === undefined) return undefined;
	if (id !== "development") throw new Error("A custom API origin is only supported for development.");
	let url: URL;
	try { url = new URL(value); }
	catch { throw new Error("Expected an HTTP(S) API origin, such as http://localhost:8080."); }
	if (!/^https?:$/.test(url.protocol) || url.username || url.password ||
		url.pathname !== "/" || url.search || url.hash || !/^[a-z][a-z0-9+.-]*:\/\/[^/?#\\\s]+\/?$/i.test(value)) {
		throw new Error("Expected an HTTP(S) API origin without credentials, path, query, or fragment.");
	}
	return url.origin === AION_ENVIRONMENTS[id].controlPlaneApiBaseUrl ? undefined : url.origin;
}

export function getControlPlaneApiBaseUrl(id: AionEnvironmentId, origin?: string): string {
	return normalizeControlPlaneOrigin(id, origin) ?? getAionEnvironment(id).controlPlaneApiBaseUrl;
}

export function getWebAppBaseUrl(id: AionEnvironmentId): string {
	return getAionEnvironment(id).webAppBaseUrl;
}

export function getAuthConfigUrl(id: AionEnvironmentId, origin?: string): string {
	return `${getControlPlaneApiBaseUrl(id, origin)}/auth/cli/config`;
}

export function getGraphQLHttpUrlForBaseUrl(baseUrl: string): string {
	const parsed = new URL(baseUrl);
	parsed.pathname = "/api/graphql";
	parsed.search = "";
	parsed.hash = "";
	return parsed.toString();
}

export function getGraphQLHttpUrl(id: AionEnvironmentId, origin?: string): string {
	return getGraphQLHttpUrlForBaseUrl(getControlPlaneApiBaseUrl(id, origin));
}

export function getGraphQLWebSocketUrl(id: AionEnvironmentId, origin?: string): string {
	const apiBaseUrl = getControlPlaneApiBaseUrl(id, origin);
	const parsed = new URL(apiBaseUrl);
	parsed.protocol = parsed.protocol === "https:" ? "wss:" : "ws:";
	parsed.pathname = "/ws/graphql";
	parsed.search = "";
	parsed.hash = "";
	return parsed.toString();
}
