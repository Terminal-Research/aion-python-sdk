export const AION_ENVIRONMENT_IDS = [
	"production",
	"staging",
	"development"
] as const;

export type AionEnvironmentId = (typeof AION_ENVIRONMENT_IDS)[number];

export interface AionEnvironment {
	id: AionEnvironmentId;
	controlPlaneApiBaseUrl: string;
}

export const DEFAULT_AION_ENVIRONMENT_ID: AionEnvironmentId = "production";

export const AION_ENVIRONMENTS: Record<AionEnvironmentId, AionEnvironment> = {
	production: {
		id: "production",
		controlPlaneApiBaseUrl: "https://api.aion.to"
	},
	staging: {
		id: "staging",
		controlPlaneApiBaseUrl: "https://api-staging.aion.to"
	},
	development: {
		id: "development",
		controlPlaneApiBaseUrl: "http://localhost:8080"
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

export function getAuthConfigUrl(id: AionEnvironmentId): string {
	return `${getControlPlaneApiBaseUrl(id)}/auth/cli/config`;
}
