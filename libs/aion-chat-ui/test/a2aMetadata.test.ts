import { describe, expect, it } from "vitest";

import {
	DISTRIBUTION_EXTENSION_URI_V1,
	TRACEABILITY_EXTENSION_URI_V1,
	generateTaskMetadata
} from "../src/lib/a2aMetadata.js";

describe("generateTaskMetadata", () => {
	it("emits both Aion extension payloads", () => {
		const metadata = generateTaskMetadata({
			agentName: "Demo Agent",
			agentUsername: "demo",
			senderId: "sender-1"
		});

		expect(metadata[DISTRIBUTION_EXTENSION_URI_V1]).toMatchObject({
			senderId: "sender-1"
		});
		expect(metadata[TRACEABILITY_EXTENSION_URI_V1]).toMatchObject({
			baggage: expect.objectContaining({
				channel: "cli"
			})
		});
	});

	it("emits the distribution payload in the camelCase shape the spec defines", () => {
		// The agent-side model accepts snake_case field names too, so a drifted
		// fixture parses anyway; assert the wire names the spec actually mandates.
		// https://docs.aion.to/a2a/extensions/aion/distribution/1.0.0
		const payload = generateTaskMetadata() as Record<string, any>;
		const distribution = payload[DISTRIBUTION_EXTENSION_URI_V1];

		expect(Object.keys(distribution.distribution)).toEqual(
			expect.arrayContaining(["id", "endpointType", "url", "identities"])
		);
		expect(Object.keys(distribution.distribution.identities[0])).toEqual(
			expect.arrayContaining([
				"kind",
				"id",
				"identityNetwork",
				"identityKind",
				"organizationId"
			])
		);
		expect(Object.keys(distribution.behavior)).toEqual(
			expect.arrayContaining(["id", "behaviorKey", "versionId"])
		);
		expect(Object.keys(distribution.environment)).toEqual(
			expect.arrayContaining([
				"id",
				"name",
				"projectId",
				"deploymentId",
				"configurationVariables"
			])
		);
		// agentType is constrained by the spec; "Deployed" is not a member.
		expect(["Personal", "Principal", "Daemon", "System"]).toContain(
			distribution.distribution.identities[0].agentType
		);
	});
});
