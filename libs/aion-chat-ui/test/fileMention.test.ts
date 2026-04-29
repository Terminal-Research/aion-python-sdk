import { describe, expect, it } from "vitest";

import {
	applyFileSuggestion,
	clearFileMention,
	getFileMentionMatch
} from "../src/lib/input/mentions/fileMention.js";

describe("fileMention", () => {
	it("detects hash-prefixed file mentions", () => {
		expect(getFileMentionMatch("#src/app.tsx")).toMatchObject({
			query: "src/app.tsx",
			start: 0,
			end: 12
		});
		expect(getFileMentionMatch("attach #../README.md")).toMatchObject({
			query: "../README.md",
			start: 7,
			end: 20
		});
		expect(getFileMentionMatch("@file:src/app.tsx")).toBeUndefined();
	});

	it("clears hash-prefixed file mentions", () => {
		expect(clearFileMention("attach #src/app.tsx")).toBe("attach");
		expect(clearFileMention("attach /tmp/app.tsx")).toBe("attach /tmp/app.tsx");
	});

	it("applies file suggestions while keeping directory suggestions navigable", () => {
		expect(
			applyFileSuggestion("attach #src/ap", {
				label: "app.tsx",
				absolutePath: "/tmp/project/src/app.tsx",
				isDirectory: false
			})
		).toBe("attach /tmp/project/src/app.tsx");

		expect(
			applyFileSuggestion("attach #src", {
				label: "src/",
				absolutePath: "/tmp/project/src",
				isDirectory: true
			})
		).toBe("attach #/tmp/project/src/");
	});
});
