import { fetchCliAuthConfig, type CliAuthConfig } from "./authConfig.js";
import {
	keyringCredentialStore,
	type CredentialStore
} from "./credentialStore.js";
import type { AionEnvironmentId } from "./environment.js";

const DEVICE_CODE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code";
const REFRESH_TOKEN_GRANT_TYPE = "refresh_token";
const DEFAULT_POLL_INTERVAL_SECONDS = 5;
const SLOW_DOWN_INCREMENT_SECONDS = 5;
const REFRESH_SKEW_SECONDS = 60;

interface DeviceAuthorizationResponse {
	device_code?: string;
	user_code?: string;
	verification_uri?: string;
	verification_uri_complete?: string;
	expires_in?: number;
	interval?: number;
}

interface WorkOSTokenResponse {
	access_token?: string;
	refresh_token?: string;
	accessToken?: string;
	refreshToken?: string;
	expires_in?: number;
	expiresIn?: number;
}

interface OAuthErrorResponse {
	error?: string;
	error_description?: string;
}

export interface DeviceAuthorizationPrompt {
	userCode: string;
	verificationUri: string;
	verificationUriComplete?: string;
	expiresInSeconds: number;
}

export interface LoginCallbacks {
	onDeviceAuthorization?: (
		prompt: DeviceAuthorizationPrompt
	) => void | Promise<void>;
	onPending?: () => void;
	onSlowDown?: (nextIntervalSeconds: number) => void;
}

export interface AuthSession {
	accessToken: string;
	refreshToken: string;
	expiresAt?: number;
}

interface AuthOptions {
	fetchImpl?: typeof fetch;
	credentialStore?: CredentialStore;
}

const accessTokenCache = new Map<AionEnvironmentId, AuthSession>();

function wait(ms: number): Promise<void> {
	return new Promise((resolve) => {
		setTimeout(resolve, ms);
	});
}

function requireString(value: string | undefined, name: string): string {
	if (!value) {
		throw new Error(`WorkOS response was missing ${name}`);
	}
	return value;
}

function normalizeTokenResponse(response: WorkOSTokenResponse): AuthSession {
	const accessToken = response.access_token ?? response.accessToken;
	const refreshToken = response.refresh_token ?? response.refreshToken;
	const expiresIn = response.expires_in ?? response.expiresIn;
	return {
		accessToken: requireString(accessToken, "access token"),
		refreshToken: requireString(refreshToken, "refresh token"),
		...(typeof expiresIn === "number"
			? { expiresAt: Date.now() + expiresIn * 1000 }
			: {})
	};
}

function tokenNeedsRefresh(session: AuthSession): boolean {
	if (!session.expiresAt) {
		return false;
	}
	return Date.now() >= session.expiresAt - REFRESH_SKEW_SECONDS * 1000;
}

async function postJson(
	url: string,
	body: Record<string, string>,
	fetchImpl: typeof fetch
): Promise<Response> {
	return fetchImpl(url, {
		method: "POST",
		headers: {
			"Content-Type": "application/json"
		},
		body: JSON.stringify(body)
	});
}

async function postForm(
	url: string,
	body: Record<string, string>,
	fetchImpl: typeof fetch
): Promise<Response> {
	return fetchImpl(url, {
		method: "POST",
		headers: {
			"Content-Type": "application/x-www-form-urlencoded"
		},
		body: new URLSearchParams(body)
	});
}

async function readOAuthError(response: Response): Promise<OAuthErrorResponse> {
	try {
		return (await response.json()) as OAuthErrorResponse;
	} catch {
		return {};
	}
}

async function requestDeviceAuthorization(
	config: CliAuthConfig,
	fetchImpl: typeof fetch
): Promise<Required<Pick<DeviceAuthorizationResponse, "device_code" | "user_code" | "verification_uri" | "expires_in">> &
	Pick<DeviceAuthorizationResponse, "verification_uri_complete" | "interval">> {
	const response = await postJson(
		config.workos.deviceAuthorizationUrl,
		{ client_id: config.workos.clientId },
		fetchImpl
	);
	if (!response.ok) {
		throw new Error(
			`Failed to start WorkOS CLI login: ${response.status} ${response.statusText}`
		);
	}

	const payload = (await response.json()) as DeviceAuthorizationResponse;
	return {
		device_code: requireString(payload.device_code, "device code"),
		user_code: requireString(payload.user_code, "user code"),
		verification_uri: requireString(payload.verification_uri, "verification URI"),
		expires_in:
			typeof payload.expires_in === "number" ? payload.expires_in : 300,
		...(payload.verification_uri_complete
			? { verification_uri_complete: payload.verification_uri_complete }
			: {}),
		...(typeof payload.interval === "number" ? { interval: payload.interval } : {})
	};
}

async function pollForToken(
	config: CliAuthConfig,
	deviceCode: string,
	expiresInSeconds: number,
	initialIntervalSeconds: number,
	callbacks: LoginCallbacks,
	fetchImpl: typeof fetch
): Promise<AuthSession> {
	let intervalSeconds = Math.max(1, initialIntervalSeconds);
	const expiresAt = Date.now() + expiresInSeconds * 1000;

	while (Date.now() < expiresAt) {
		await wait(intervalSeconds * 1000);
		const response = await postForm(
			config.workos.tokenUrl,
			{
				grant_type: DEVICE_CODE_GRANT_TYPE,
				device_code: deviceCode,
				client_id: config.workos.clientId
			},
			fetchImpl
		);

		if (response.ok) {
			return normalizeTokenResponse((await response.json()) as WorkOSTokenResponse);
		}

		const error = await readOAuthError(response);
		switch (error.error) {
			case "authorization_pending":
				callbacks.onPending?.();
				break;
			case "slow_down":
				intervalSeconds += SLOW_DOWN_INCREMENT_SECONDS;
				callbacks.onSlowDown?.(intervalSeconds);
				break;
			case "access_denied":
				throw new Error("WorkOS login was denied.");
			case "expired_token":
				throw new Error("WorkOS login expired before authorization completed.");
			default:
				throw new Error(
					`WorkOS login failed: ${
						error.error_description ?? error.error ?? response.statusText
					}`
				);
		}
	}

	throw new Error("WorkOS login expired before authorization completed.");
}

async function refreshSession(
	environmentId: AionEnvironmentId,
	config: CliAuthConfig,
	refreshToken: string,
	fetchImpl: typeof fetch,
	credentialStore: CredentialStore
): Promise<AuthSession> {
	if (!config.workos.supportsDirectRefresh) {
		throw new Error("This Aion API environment does not support direct CLI refresh.");
	}

	const response = await postForm(
		config.workos.refreshTokenUrl,
		{
			grant_type: REFRESH_TOKEN_GRANT_TYPE,
			refresh_token: refreshToken,
			client_id: config.workos.clientId
		},
		fetchImpl
	);
	if (!response.ok) {
		const error = await readOAuthError(response);
		throw new Error(
			`WorkOS token refresh failed: ${
				error.error_description ?? error.error ?? response.statusText
			}`
		);
	}

	const session = normalizeTokenResponse((await response.json()) as WorkOSTokenResponse);
	await credentialStore.setRefreshToken(environmentId, session.refreshToken);
	accessTokenCache.set(environmentId, session);
	return session;
}

export async function loginWithWorkOS(
	environmentId: AionEnvironmentId,
	callbacks: LoginCallbacks = {},
	options: AuthOptions = {}
): Promise<AuthSession> {
	const fetchImpl = options.fetchImpl ?? fetch;
	const credentialStore = options.credentialStore ?? keyringCredentialStore;
	const config = await fetchCliAuthConfig(environmentId, fetchImpl);
	const deviceAuthorization = await requestDeviceAuthorization(config, fetchImpl);
	await callbacks.onDeviceAuthorization?.({
		userCode: deviceAuthorization.user_code,
		verificationUri: deviceAuthorization.verification_uri,
		...(deviceAuthorization.verification_uri_complete
			? { verificationUriComplete: deviceAuthorization.verification_uri_complete }
			: {}),
		expiresInSeconds: deviceAuthorization.expires_in
	});

	const session = await pollForToken(
		config,
		deviceAuthorization.device_code,
		deviceAuthorization.expires_in,
		deviceAuthorization.interval ?? DEFAULT_POLL_INTERVAL_SECONDS,
		callbacks,
		fetchImpl
	);
	await credentialStore.setRefreshToken(environmentId, session.refreshToken);
	accessTokenCache.set(environmentId, session);
	return session;
}

export async function getStoredAccessToken(
	environmentId: AionEnvironmentId,
	options: AuthOptions = {}
): Promise<string | undefined> {
	const cached = accessTokenCache.get(environmentId);
	if (cached && !tokenNeedsRefresh(cached)) {
		return cached.accessToken;
	}

	const fetchImpl = options.fetchImpl ?? fetch;
	const credentialStore = options.credentialStore ?? keyringCredentialStore;
	const refreshToken = await credentialStore.getRefreshToken(environmentId);
	if (!refreshToken) {
		return undefined;
	}

	const config = await fetchCliAuthConfig(environmentId, fetchImpl);
	const session = await refreshSession(
		environmentId,
		config,
		refreshToken,
		fetchImpl,
		credentialStore
	);
	return session.accessToken;
}
