import { describe, expect, it } from "vitest";

import {
	AION_ENVIRONMENTS,
	getAuthConfigUrl,
	getControlPlaneApiBaseUrl
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
	});

	it("derives auth config URLs from the control plane API base", () => {
		expect(getControlPlaneApiBaseUrl("staging")).toBe(
			"https://api-staging.aion.to"
		);
		expect(getAuthConfigUrl("staging")).toBe(
			"https://api-staging.aion.to/auth/cli/config"
		);
	});
});
