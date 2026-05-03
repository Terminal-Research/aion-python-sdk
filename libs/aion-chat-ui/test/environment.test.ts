import { describe, expect, it } from "vitest";

import {
	AION_ENVIRONMENTS,
	getAuthConfigUrl,
	getControlPlaneApiBaseUrl,
	getGraphQLHttpUrl,
	getGraphQLWebSocketUrl,
	getWebAppBaseUrl
} from "../src/lib/environment.js";

describe("Aion environments", () => {
	it("keeps control plane API URLs separate from A2A endpoints", () => {
		expect(AION_ENVIRONMENTS.production.controlPlaneApiBaseUrl).toBe(
			"https://api.aion.to"
		);
		expect(AION_ENVIRONMENTS.staging.controlPlaneApiBaseUrl).toBe(
			"https://api-staging.aion.to"
		);
		expect(AION_ENVIRONMENTS.development.controlPlaneApiBaseUrl).toBe(
			"http://localhost:8080"
		);
		expect(AION_ENVIRONMENTS.production.webAppBaseUrl).toBe(
			"https://app.aion.to"
		);
		expect(AION_ENVIRONMENTS.staging.webAppBaseUrl).toBe(
			"https://app-staging.aion.to"
		);
		expect(AION_ENVIRONMENTS.development.webAppBaseUrl).toBe(
			"https://localhost:3000"
		);
	});

	it("derives control-plane URLs from the API base", () => {
		expect(getControlPlaneApiBaseUrl("staging")).toBe(
			"https://api-staging.aion.to"
		);
		expect(getAuthConfigUrl("staging")).toBe(
			"https://api-staging.aion.to/auth/cli/config"
		);
		expect(getGraphQLHttpUrl("staging")).toBe(
			"https://api-staging.aion.to/api/graphql"
		);
		expect(getGraphQLWebSocketUrl("staging")).toBe(
			"wss://api-staging.aion.to/ws/graphql"
		);
		expect(getGraphQLWebSocketUrl("development")).toBe(
			"ws://localhost:8080/ws/graphql"
		);
		expect(getWebAppBaseUrl("production")).toBe("https://app.aion.to");
	});
});
