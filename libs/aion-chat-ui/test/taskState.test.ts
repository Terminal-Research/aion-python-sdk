import { describe, expect, it } from "vitest";

import { isTerminalTaskState } from "../src/lib/taskState.js";

describe("taskState", () => {
	it("identifies terminal task states", () => {
		expect(isTerminalTaskState("completed")).toBe(true);
		expect(isTerminalTaskState("canceled")).toBe(true);
		expect(isTerminalTaskState("failed")).toBe(true);
		expect(isTerminalTaskState("rejected")).toBe(true);
	});

	it("leaves non-terminal task states active", () => {
		expect(isTerminalTaskState("submitted")).toBe(false);
		expect(isTerminalTaskState("working")).toBe(false);
		expect(isTerminalTaskState("input-required")).toBe(false);
		expect(isTerminalTaskState("auth-required")).toBe(false);
		expect(isTerminalTaskState("unknown")).toBe(false);
	});
});
