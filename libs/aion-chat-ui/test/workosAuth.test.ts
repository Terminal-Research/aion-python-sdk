import { afterEach, describe, expect, it, vi } from "vitest";

import type { CredentialStore } from "../src/lib/credentialStore.js";
import { getStoredAccessToken } from "../src/lib/workosAuth.js";


function createJwt(payload: Record<string, unknown>): string {
	const header = Buffer.from(JSON.stringify({ alg: "none", typ: "JWT" })).toString(
		"base64url"
	);
	const body = Buffer.from(JSON.stringify(payload)).toString("base64url");
	return `${header}.${body}.signature`;
}

afterEach(() => {
	vi.useRealTimers();
});

const configResponse = {
	apiBaseUrl: "http://localhost:8080",
	authProvider: "workos",
	authMode: "cliDevice",
	workos: {
		clientId: "client_123",
		issuer: "https://api.workos.com/user_management/client_123",
		deviceAuthorizationUrl:
			"https://api.workos.com/user_management/authorize/device",
		tokenUrl: "https://api.workos.com/user_management/authenticate",
		refreshTokenUrl: "https://api.workos.com/user_management/authenticate",
		supportsDirectRefresh: true
	}
};

describe("getStoredAccessToken", () => {
	it("refreshes a stored WorkOS token and rotates the refresh token", async () => {
		const storedTokens: string[] = [];
		const credentialStore: CredentialStore = {
			getRefreshToken: async () => "old-refresh-token",
			setRefreshToken: async (_environmentId, refreshToken) => {
				storedTokens.push(refreshToken);
			},
			deleteRefreshToken: async () => undefined
		};
		const fetchImpl = vi
			.fn()
			.mockResolvedValueOnce({
				ok: true,
				json: async () => configResponse
			})
			.mockResolvedValueOnce({
				ok: true,
				json: async () => ({
					access_token: "access-token",
					refresh_token: "new-refresh-token",
					expires_in: 3600
				})
			}) as unknown as typeof fetch;

		await expect(
			getStoredAccessToken("development", { credentialStore, fetchImpl })
		).resolves.toBe("access-token");

		expect(fetchImpl).toHaveBeenNthCalledWith(
			1,
			"http://localhost:8080/auth/cli/config"
		);
		const [, refreshInit] = vi.mocked(fetchImpl).mock.calls[1] ?? [];
		expect(String((refreshInit as RequestInit).body)).toContain(
			"grant_type=refresh_token"
		);
		expect(String((refreshInit as RequestInit).body)).toContain(
			"refresh_token=old-refresh-token"
		);
		expect(storedTokens).toEqual(["new-refresh-token"]);
	});
	it("uses the access token JWT exp claim when WorkOS omits expires_in", async () => {
		vi.useFakeTimers();
		vi.setSystemTime(new Date("2026-06-03T00:00:00.000Z"));
		let storedRefreshToken = "old-refresh-token";
		const credentialStore: CredentialStore = {
			getRefreshToken: async () => storedRefreshToken,
			setRefreshToken: async (_environmentId, refreshToken) => {
				storedRefreshToken = refreshToken;
			},
			deleteRefreshToken: async () => undefined
		};
		const firstAccessToken = createJwt({ exp: 1_780_444_980 });
		const secondAccessToken = createJwt({ exp: 1_780_448_400 });
		const fetchImpl = vi
			.fn()
			.mockResolvedValueOnce({
				ok: true,
				json: async () => configResponse
			})
			.mockResolvedValueOnce({
				ok: true,
				json: async () => ({
					access_token: firstAccessToken,
					refresh_token: "new-refresh-token"
				})
			})
			.mockResolvedValueOnce({
				ok: true,
				json: async () => configResponse
			})
			.mockResolvedValueOnce({
				ok: true,
				json: async () => ({
					access_token: secondAccessToken,
					refresh_token: "rotated-refresh-token"
				})
			}) as unknown as typeof fetch;

		await expect(
			getStoredAccessToken("staging", { credentialStore, fetchImpl })
		).resolves.toBe(firstAccessToken);

		vi.setSystemTime(new Date("2026-06-03T00:02:01.000Z"));

		await expect(
			getStoredAccessToken("staging", { credentialStore, fetchImpl })
		).resolves.toBe(secondAccessToken);

		const [, secondRefreshInit] = vi.mocked(fetchImpl).mock.calls[3] ?? [];
		expect(String((secondRefreshInit as RequestInit).body)).toContain(
			"refresh_token=new-refresh-token"
		);
		expect(storedRefreshToken).toBe("rotated-refresh-token");
	});

	it("force refreshes a cached access token", async () => {
		const credentialStore: CredentialStore = {
			getRefreshToken: async () => "stored-refresh-token",
			setRefreshToken: async () => undefined,
			deleteRefreshToken: async () => undefined
		};
		const firstAccessToken = createJwt({ exp: 4_102_444_800 });
		const secondAccessToken = createJwt({ exp: 4_102_444_800 });
		const fetchImpl = vi
			.fn()
			.mockResolvedValueOnce({
				ok: true,
				json: async () => configResponse
			})
			.mockResolvedValueOnce({
				ok: true,
				json: async () => ({
					access_token: firstAccessToken,
					refresh_token: "first-refresh-token"
				})
			})
			.mockResolvedValueOnce({
				ok: true,
				json: async () => configResponse
			})
			.mockResolvedValueOnce({
				ok: true,
				json: async () => ({
					access_token: secondAccessToken,
					refresh_token: "second-refresh-token"
				})
			}) as unknown as typeof fetch;

		await expect(
			getStoredAccessToken("production", { credentialStore, fetchImpl })
		).resolves.toBe(firstAccessToken);
		await expect(
			getStoredAccessToken("production", {
				credentialStore,
				fetchImpl,
				forceRefresh: true
			})
		).resolves.toBe(secondAccessToken);

		expect(vi.mocked(fetchImpl)).toHaveBeenCalledTimes(4);
	});

});
