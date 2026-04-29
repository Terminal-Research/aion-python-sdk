import { describe, expect, it } from "vitest";

import {
	clearLeadingSlashDraft,
	filterSlashCommands,
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
			"/login",
			"/request",
			"/response"
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
		expect(filterSlashCommands("c").map((command) => command.label)).toEqual(["/clear"]);
	});

	it("clears the leading slash draft while preserving leading whitespace", () => {
		expect(clearLeadingSlashDraft("/request")).toBe("");
		expect(clearLeadingSlashDraft("  /request")).toBe("  ");
		expect(clearLeadingSlashDraft("hello")).toBe("hello");
	});

	it("renders stable labels for persisted modes", () => {
		expect(getRequestModeLabel("send-message")).toBe("Send message");
		expect(getResponseModeLabel("a2a-protocol")).toBe("A2A protocol");
	});
});
