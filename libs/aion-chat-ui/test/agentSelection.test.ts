import { describe, expect, it } from "vitest";

import {
	clearAgentMention,
	getAgentMentionMatch,
	parseAgentSelection
} from "../src/lib/agentSelection.js";

describe("agentSelection", () => {
	it("detects agent mention queries at the end of the draft", () => {
		expect(getAgentMentionMatch("hello @comm")).toEqual({
			query: "comm",
			start: 6,
			end: 11
		});
	});

	it("clears only the trailing mention token after agent selection", () => {
		expect(clearAgentMention("hello @comm")).toBe("hello");
		expect(clearAgentMention("@comm")).toBe("");
	});

	it("extracts a leading explicit agent selection from the draft", () => {
		expect(parseAgentSelection("@command-agent tell me a joke", ["command-agent"]))
			.toEqual({
				agentId: "command-agent",
				message: "tell me a joke"
			});
	});
});
