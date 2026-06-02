import { describe, expect, it } from "vitest";

import type { AgentSourceRecord } from "../src/lib/agents/model.js";
import {
	clearLeadingSlashDraft,
	filterSlashCommands,
	formatAgentSourcesList,
	getLeadingSlashQuery,
	getRequestModeLabel,
	getResponseModeLabel
} from "../src/lib/slashCommands.js";

describe("slashCommands", () => {
	it("detects a leading slash command at the first non-whitespace token", () => {
		expect(getLeadingSlashQuery("/req")).toBe("req");
		expect(getLeadingSlashQuery("   /response")).toBe("response");
		expect(getLeadingSlashQuery("hello /response")).toBeUndefined();
	});

	it("filters slash commands alphabetically by prefix", () => {
		expect(filterSlashCommands("").map((command) => command.label)).toEqual([
			"/clear",
			"/copy",
			"/exit",
			"/login",
			"/request",
			"/response",
			"/sources"
		]);
		expect(filterSlashCommands("e").map((command) => command.label)).toEqual([
			"/exit"
		]);
		expect(filterSlashCommands("l").map((command) => command.label)).toEqual([
			"/login"
		]);
		expect(filterSlashCommands("r").map((command) => command.label)).toEqual([
			"/request",
			"/response"
		]);
		expect(filterSlashCommands("request").map((command) => command.label)).toEqual([
			"/request"
		]);
		expect(filterSlashCommands("c").map((command) => command.label)).toEqual([
			"/clear",
			"/copy"
		]);
		expect(filterSlashCommands("s").map((command) => command.label)).toEqual([
			"/sources"
		]);
	});

	it("clears the leading slash draft while preserving leading whitespace", () => {
		expect(clearLeadingSlashDraft("/request")).toBe("");
		expect(clearLeadingSlashDraft("  /request")).toBe("  ");
		expect(clearLeadingSlashDraft("hello")).toBe("hello");
	});

	it("formats the sources command output with a spacer after the heading", () => {
		const sources: AgentSourceRecord[] = [
			{
				sourceKey: "aion-registry-development",
				type: "registry",
				description: "Aion development registry",
				url: "http://localhost:8080",
				enabled: true,
				isDefault: true,
				status: "unavailable",
				lastError: "/login to authenticate."
			},
			{
				sourceKey: "default-localhost-8000",
				type: "manifest",
				description: "Local Aion SDK server",
				url: "http://localhost:8000",
				enabled: true,
				isDefault: true,
				status: "available"
			}
		];

		expect(formatAgentSourcesList(sources)).toBe(
			[
				"Agent sources",
				"",
				"aion-registry-development",
				"Type: registry",
				"Description: Aion development registry",
				"URL: http://localhost:8080",
				"Status: unavailable",
				"Reason: /login to authenticate.",
				"",
				"default-localhost-8000",
				"Type: manifest",
				"Description: Local Aion SDK server",
				"URL: http://localhost:8000",
				"Status: available",
				"",
				"Registry sources update when Aion Chat starts and whenever /sources is run."
			].join("\n")
		);
	});

	it("renders stable labels for persisted modes", () => {
		expect(getRequestModeLabel("send-message")).toBe("Send message");
		expect(getResponseModeLabel("a2a-protocol")).toBe("A2A protocol");
	});
});
