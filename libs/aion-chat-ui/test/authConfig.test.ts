import { describe, expect, it, vi } from "vitest";

import { fetchCliAuthConfig, parseCliAuthConfig } from "../src/lib/authConfig.js";

const validConfig = {
	apiBaseUrl: "https://api.aion.to",
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

describe("parseCliAuthConfig", () => {
	it("accepts the lower camel case CLI auth config contract", () => {
		expect(parseCliAuthConfig(validConfig)).toEqual(validConfig);
	});

	it("rejects unsupported auth providers", () => {
		expect(() =>
			parseCliAuthConfig({
				...validConfig,
				authProvider: "other"
			})
		).toThrow("Unsupported CLI auth provider");
	});
});

describe("fetchCliAuthConfig", () => {
	it("fetches config from the selected environment", async () => {
		const fetchImpl = vi.fn(async () => ({
			ok: true,
			json: async () => validConfig
		})) as unknown as typeof fetch;

		await expect(fetchCliAuthConfig("development", fetchImpl)).resolves.toEqual(
			validConfig
		);
		expect(fetchImpl).toHaveBeenCalledWith("http://localhost:8080/auth/cli/config");
	});
});
