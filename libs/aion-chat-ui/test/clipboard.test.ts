import { spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import { Writable } from "node:stream";

import { describe, expect, it, vi } from "vitest";

import {
	buildClipboardWriteCommands,
	writeClipboard
} from "../src/lib/clipboard.js";

describe("clipboard helpers", () => {
	it("builds platform clipboard commands", () => {
		expect(buildClipboardWriteCommands("darwin")).toEqual([
			{ command: "pbcopy", args: [] }
		]);
		expect(buildClipboardWriteCommands("win32")).toEqual([
			{ command: "clip", args: [], options: { windowsHide: true } }
		]);
		expect(
			buildClipboardWriteCommands("linux", { WAYLAND_DISPLAY: "wayland-0" })[0]
		).toEqual({ command: "wl-copy", args: [] });
	});

	it("writes clipboard content through the first successful command", async () => {
		const attempts: string[] = [];
		const writes = new Map<string, string>();
		const spawnImpl = vi.fn((command: string) => {
			attempts.push(command);
			const child = new EventEmitter() as EventEmitter & {
				stdin: Writable;
			};
			child.stdin = new Writable({
				write(chunk, _encoding, callback) {
					writes.set(command, `${writes.get(command) ?? ""}${chunk.toString()}`);
					callback();
				}
			});
			queueMicrotask(() => child.emit("exit", command === "bad-copy" ? 1 : 0));
			return child;
		}) as unknown as typeof spawn;

		await writeClipboard("copy me", {
			commands: [
				{ command: "bad-copy", args: [] },
				{ command: "good-copy", args: [] }
			],
			spawnImpl
		});

		expect(attempts).toEqual(["bad-copy", "good-copy"]);
		expect(writes.get("good-copy")).toBe("copy me");
	});
});
