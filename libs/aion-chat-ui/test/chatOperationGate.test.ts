import { describe, expect, it } from "vitest";
import { ChatOperationGate } from "../src/lib/chatOperationGate.js";

describe("foreground and workspace operation exclusion", () => {
	it("holds a reply's guard from preparation through final save", async () => {
		const gate = new ChatOperationGate();
		const reply = gate.begin("reply")!;
		for (const stage of ["prepare", "send", "stream", "save"]) {
			await Promise.resolve(stage);
			expect(gate.begin("workspace")).toBeUndefined();
		}
		expect(gate.reason).toContain("reply to finish");
		reply.finish();
		expect(gate.begin("workspace")).toBeDefined();
	});
	it("rejects overlapping workspace choices and messages without starting or queuing them", () => {
		const gate = new ChatOperationGate();
		const first = gate.begin("workspace")!;
		expect(gate.begin("workspace")).toBeUndefined();
		expect(gate.begin("reply")).toBeUndefined();
		first.finish();
		expect(gate.busy).toBe(false);
		expect(gate.begin("reply")).toBeDefined();
	});
	it("cancels on environment change and an old callback cannot release a newer operation", () => {
		const gate = new ChatOperationGate();
		const old = gate.begin("workspace")!;
		gate.cancel();
		expect(old.signal.aborted).toBe(true);
		const current = gate.begin("workspace")!;
		old.finish();
		expect(gate.busy).toBe(true);
		current.finish();
		expect(gate.busy).toBe(false);
	});
});
