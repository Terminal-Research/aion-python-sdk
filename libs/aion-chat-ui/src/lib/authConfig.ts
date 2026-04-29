import { type AionEnvironmentId, getAuthConfigUrl } from "./environment.js";

export interface WorkOSCliAuthConfig {
	clientId: string;
	issuer: string;
	deviceAuthorizationUrl: string;
	tokenUrl: string;
	refreshTokenUrl: string;
	supportsDirectRefresh: boolean;
}

export interface CliAuthConfig {
	apiBaseUrl: string;
	authProvider: "workos";
	authMode: "cliDevice";
	workos: WorkOSCliAuthConfig;
}

function isObject(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function readString(
	value: Record<string, unknown>,
	key: string,
	path: string
): string {
	const next = value[key];
	if (typeof next !== "string" || !next.trim()) {
		throw new Error(`Invalid CLI auth config: missing ${path}`);
	}
	return next;
}

function readBoolean(
	value: Record<string, unknown>,
	key: string,
	path: string
): boolean {
	const next = value[key];
	if (typeof next !== "boolean") {
		throw new Error(`Invalid CLI auth config: missing ${path}`);
	}
	return next;
}

export function parseCliAuthConfig(value: unknown): CliAuthConfig {
	if (!isObject(value)) {
		throw new Error("Invalid CLI auth config: expected object response");
	}

	const authProvider = readString(value, "authProvider", "authProvider");
	const authMode = readString(value, "authMode", "authMode");
	if (authProvider !== "workos") {
		throw new Error(`Unsupported CLI auth provider: ${authProvider}`);
	}
	if (authMode !== "cliDevice") {
		throw new Error(`Unsupported CLI auth mode: ${authMode}`);
	}

	const workos = value.workos;
	if (!isObject(workos)) {
		throw new Error("Invalid CLI auth config: missing workos");
	}

	return {
		apiBaseUrl: readString(value, "apiBaseUrl", "apiBaseUrl"),
		authProvider,
		authMode,
		workos: {
			clientId: readString(workos, "clientId", "workos.clientId"),
			issuer: readString(workos, "issuer", "workos.issuer"),
			deviceAuthorizationUrl: readString(
				workos,
				"deviceAuthorizationUrl",
				"workos.deviceAuthorizationUrl"
			),
			tokenUrl: readString(workos, "tokenUrl", "workos.tokenUrl"),
			refreshTokenUrl: readString(
				workos,
				"refreshTokenUrl",
				"workos.refreshTokenUrl"
			),
			supportsDirectRefresh: readBoolean(
				workos,
				"supportsDirectRefresh",
				"workos.supportsDirectRefresh"
			)
		}
	};
}

export async function fetchCliAuthConfig(
	environmentId: AionEnvironmentId,
	fetchImpl: typeof fetch = fetch
): Promise<CliAuthConfig> {
	const url = getAuthConfigUrl(environmentId);
	const response = await fetchImpl(url);
	if (!response.ok) {
		throw new Error(`Failed to fetch CLI auth config from ${url}: ${response.status}`);
	}

	return parseCliAuthConfig(await response.json());
}
