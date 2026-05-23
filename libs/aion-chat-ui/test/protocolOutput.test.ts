import { describe, expect, it } from "vitest";

import {
	formatProtocolPayload,
	formatProtocolPayloadAsJson
} from "../src/lib/protocolOutput.js";

describe("protocol output", () => {
	it("formats protocol payloads as display YAML and copyable JSON", () => {
		const payload = {
			kind: "message",
			role: "agent",
			parts: [{ kind: "text", text: "answer" }]
		};

		expect(formatProtocolPayload(payload)).toContain("```yaml");
		expect(JSON.parse(formatProtocolPayloadAsJson(payload))).toEqual(payload);
		expect(formatProtocolPayloadAsJson(payload)).toContain('\n  "role": "agent"');
	});
});
