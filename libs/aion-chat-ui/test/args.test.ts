import { describe, expect, it } from "vitest";

import { parseArgs } from "../src/args.js";

describe("parseArgs", () => {
	it("parses the core chat2 flags", () => {
		expect(
			parseArgs([
				"--url",
				"http://localhost:8000",
				"--agent-id",
				"demo-agent",
				"--token",
				"secret-token",
				"--header",
				"X-Test=one",
				"--push-notifications",
				"--push-receiver",
				"http://localhost:5050"
			])
		).toEqual({
			url: "http://localhost:8000",
			agentId: "demo-agent",
			token: "secret-token",
			headers: { "X-Test": "one" },
			pushNotifications: true,
			pushReceiver: "http://localhost:5050"
		});
	});

	it("defaults to the local proxy when no endpoint is provided", () => {
		expect(parseArgs([]).url).toBe("http://localhost:8000");
	});
});
