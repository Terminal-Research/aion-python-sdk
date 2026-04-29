import { describe, expect, it } from "vitest";

import { parseArgs, parseCliArgs } from "../src/args.js";

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

	it("leaves the A2A endpoint unset when no endpoint is provided", () => {
		expect(parseArgs([]).url).toBeUndefined();
	});

	it("parses login as a command", () => {
		expect(parseCliArgs(["login"])).toEqual({
			kind: "login"
		});
	});

	it("parses hidden environment commands", () => {
		expect(parseCliArgs(["environment", "development"])).toEqual({
			kind: "environment",
			environmentId: "development"
		});
		expect(parseCliArgs(["env", "staging"])).toEqual({
			kind: "environment",
			environmentId: "staging"
		});
	});
});
