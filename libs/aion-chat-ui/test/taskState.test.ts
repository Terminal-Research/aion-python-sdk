import { TaskState } from "@a2a-js/sdk";
import { describe, expect, it } from "vitest";

import {
	isTaskContinuationState,
	isTerminalTaskState
} from "../src/lib/taskState.js";

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

	it("identifies interrupted task states that should receive follow-up input", () => {
		expect(isTaskContinuationState("input-required")).toBe(true);
		expect(isTaskContinuationState("auth-required")).toBe(true);
		expect(isTaskContinuationState(TaskState.TASK_STATE_INPUT_REQUIRED)).toBe(true);
		expect(isTaskContinuationState(TaskState.TASK_STATE_AUTH_REQUIRED)).toBe(true);
	});

	it("does not treat ordinary active or terminal states as follow-up tasks", () => {
		expect(isTaskContinuationState("submitted")).toBe(false);
		expect(isTaskContinuationState("working")).toBe(false);
		expect(isTaskContinuationState("completed")).toBe(false);
		expect(isTaskContinuationState(TaskState.TASK_STATE_WORKING)).toBe(false);
		expect(isTaskContinuationState(TaskState.TASK_STATE_COMPLETED)).toBe(false);
	});
});
