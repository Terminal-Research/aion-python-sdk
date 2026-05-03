import { describe, expect, it, vi } from "vitest";

import {
	parseArgs,
	parseCliArgs,
	parseRunArgs,
	printRunHelp
} from "../src/args.js";

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

	it("parses headless run flags and message text", () => {
		expect(
			parseRunArgs([
				"--url",
				"http://localhost:8000",
				"--agent-id",
				"demo-agent",
				"--agent",
				"@team-agent",
				"--request-mode",
				"stream",
				"--response-mode",
				"a2a",
				"hello",
				"there"
			])
		).toEqual({
			url: "http://localhost:8000",
			agentId: "demo-agent",
			agentSelector: "@team-agent",
			token: undefined,
			headers: {},
			pushNotifications: false,
			pushReceiver: "http://localhost:5000",
			requestMode: "streaming-message",
			responseMode: "a2a-protocol",
			readMessageFromStdin: false,
			message: "hello there"
		});
	});

	it("parses run as a command", () => {
		expect(parseCliArgs(["run", "--agent", "team", "-"])).toEqual({
			kind: "run",
			options: {
				agentId: undefined,
				agentSelector: "team",
				token: undefined,
				headers: {},
				pushNotifications: false,
				pushReceiver: "http://localhost:5000",
				requestMode: "send-message",
				responseMode: "message-output",
				readMessageFromStdin: true
			}
		});
	});

	it("prints dedicated headless run help", () => {
		let output = "";
		const write = vi
			.spyOn(process.stdout, "write")
			.mockImplementation((chunk: string | Uint8Array) => {
				output += chunk.toString();
				return true;
			});

		try {
			printRunHelp();
		} finally {
			write.mockRestore();
		}

		expect(output).toContain("Usage:\n  aio run [options] [message]");
		expect(output).toContain("Agent selection:");
		expect(output).toContain("Streaming a2a mode writes JSONL events.");
	});
});
