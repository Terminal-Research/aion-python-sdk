import { describe, expect, it, vi } from "vitest";

import type { CredentialStore } from "../src/lib/credentialStore.js";
import { getStoredAccessToken } from "../src/lib/workosAuth.js";

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
});
