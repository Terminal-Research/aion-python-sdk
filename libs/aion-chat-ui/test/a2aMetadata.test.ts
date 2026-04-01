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
			sender_id: "sender-1"
		});
		expect(metadata[TRACEABILITY_EXTENSION_URI_V1]).toMatchObject({
			baggage: expect.objectContaining({
				channel: "cli"
			})
		});
	});
});
