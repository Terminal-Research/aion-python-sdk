import { PassThrough, Writable } from "node:stream";
import React from "react";
import { render as renderInk } from "ink";
import { render } from "ink-testing-library";
import { describe, expect, it, vi } from "vitest";

import {
	ChatComposer,
	wrapComposerDraft
} from "../src/components/ChatComposer.js";
import { ChatSession } from "../src/components/ChatSession.js";
import { SystemNotificationStack } from "../src/components/SystemNotificationStack.js";
import { HomeScreen } from "../src/components/HomeScreen.js";
import {
	MessageBubble,
	WorkingIndicator
} from "../src/components/MessageBubble.js";

class TestTerminal extends Writable {
	readonly columns = 100;
	readonly rows = 40;
	// Avoid cli-cursor's process-exit hook; Ink still emits requested cursor moves.
	readonly isTTY = false;
	output = "";

	override _write(
		chunk: Buffer | string,
		_encoding: BufferEncoding,
		callback: (error?: Error | null) => void
	): void {
		this.output += chunk.toString();
		callback();
	}
}

describe("Ink components", () => {
	it("renders the agent picker and hides the footer while the @ menu is open", () => {
		const app = render(
			<ChatComposer
				draft=""
				cursorOffset={0}
				activeAgentId={undefined}
				discoveredCount={2}
				pushState="Disabled"
				streamState="Idle"
				agentSuggestions={[
					{
						agentKey: "default-localhost-8000:command-agent",
						id: "command-agent",
						sourceName: "default-localhost-8000",
						description: "Runs command workflows."
					},
					{
						agentKey: "aion-registry-development:research-chat",
						id: "research-chat",
						sourceName: "aion-registry-development",
						description: "Answers research questions."
					}
				]}
				selectedSuggestionIndex={0}
				slashCommands={[]}
				selectedSlashCommandIndex={0}
				fileSuggestions={[]} selectedFileSuggestionIndex={0} slashMenuVisible={false}
			/>
		);

		expect(app.lastFrame()).toContain("Send message");
		expect(app.lastFrame()).toContain("Discovered: 2");
		expect(app.lastFrame()).toContain("@command-agent");
		expect(app.lastFrame()).toContain("default-localhost-8000");
		expect(app.lastFrame()).toContain("Runs command workflows.");
		expect(app.lastFrame()).toContain("aion-registry-development");
		expect(app.lastFrame()).toContain("Answers research questions.");
		expect(app.lastFrame()).not.toContain("Stream: Idle");
		expect(app.lastFrame()).not.toContain("Push:");
		expect(app.lastFrame()).not.toContain("Ctrl+C");
		app.unmount();
	});

	it("truncates long agent descriptions in the picker", () => {
		const description =
			"This description is intentionally long enough to exceed the picker row width and must not wrap onto another terminal line.";
		const app = render(
			<ChatComposer
				draft=""
				cursorOffset={0}
				activeAgentId={undefined}
				discoveredCount={1}
				pushState="Disabled"
				streamState="Idle"
				agentSuggestions={[
					{
						agentKey: "aion-registry-development:command-agent",
						id: "command-agent",
						sourceName: "aion-registry-development",
						description
					}
				]}
				selectedSuggestionIndex={0}
				slashCommands={[]}
				selectedSlashCommandIndex={0}
				fileSuggestions={[]} selectedFileSuggestionIndex={0} slashMenuVisible={false}
			/>
		);

		expect(app.lastFrame()).toContain("@command-agent");
		expect(app.lastFrame()).toContain("aion-registry-development");
		expect(app.lastFrame()).not.toContain(description);
		expect(app.lastFrame()).toContain("...");
		app.unmount();
	});

	it("renders the slash command list and hides the footer while it is open", () => {
		const app = render(
			<ChatComposer
				draft="/re"
				cursorOffset={3}
				activeAgentId="command-agent"
				discoveredCount={2}
				pushState="Disabled"
				streamState="Idle"
				agentSuggestions={[]}
				selectedSuggestionIndex={0}
				slashCommands={[
					{
						label: "/request",
						description: "Choose how Aion Chat sends requests to the agents."
					},
					{
						label: "/response",
						description: "Choose how Aion Chat renders responses from the agents."
					}
				]}
				selectedSlashCommandIndex={0}
				fileSuggestions={[]} selectedFileSuggestionIndex={0} slashMenuVisible={true}
			/>
		);

		expect(app.lastFrame()).toContain("/request");
		expect(app.lastFrame()).toContain("/response");
		expect(app.lastFrame()).toContain("Choose how Aion Chat sends requests to the agents.");
		expect(app.lastFrame()).not.toContain("Stream: Idle");
		expect(app.lastFrame()).not.toContain("Ctrl+C");
		app.unmount();
	});

	it("renders the clear command in the slash command list", () => {
		const app = render(
			<ChatComposer
				draft="/c"
				cursorOffset={2}
				activeAgentId="command-agent"
				discoveredCount={2}
				pushState="Disabled"
				streamState="Idle"
				agentSuggestions={[]}
				selectedSuggestionIndex={0}
				slashCommands={[
					{
						label: "/clear",
						description: "Clear terminal output and start a fresh chat context."
					}
				]}
				selectedSlashCommandIndex={0}
				fileSuggestions={[]} selectedFileSuggestionIndex={0} slashMenuVisible={true}
			/>
		);

		expect(app.lastFrame()).toContain("/clear");
		expect(app.lastFrame()).toContain(
			"Clear terminal output and start a fresh chat context."
		);
		expect(app.lastFrame()).not.toContain("Ctrl+C");
		app.unmount();
	});

	it("renders the slash submenu inside the composer", () => {
		const app = render(
			<ChatComposer
				draft=""
				cursorOffset={0}
				activeAgentId="command-agent"
				discoveredCount={2}
				pushState="Disabled"
				streamState="Idle"
				agentSuggestions={[]}
				selectedSuggestionIndex={0}
				slashCommands={[]}
				selectedSlashCommandIndex={0}
				fileSuggestions={[]} selectedFileSuggestionIndex={0} slashMenuVisible={false}
				slashSubmenu={{
					title: "Request Mode",
					subtitle: "Choose how Aion Chat sends requests to the agents.",
					options: [
						{
							label: "Send message",
							description: "Send a synchronous request and wait for a single reply."
						},
						{
							label: "Streaming message",
							description:
								"Send a streaming request and render incremental events as they arrive."
						}
					],
					selectedIndex: 0
				}}
			/>
		);

		expect(app.lastFrame()).toContain("Request Mode");
		expect(app.lastFrame()).toContain("1. Send message");
		expect(app.lastFrame()).toContain("2. Streaming message");
		expect(app.lastFrame()).not.toContain("Ctrl+C");
		app.unmount();
	});

	it("renders the composer clear hint when draft content exists", () => {
		const app = render(
			<ChatComposer
				draft="hello"
				cursorOffset={5}
				activeAgentId="command-agent"
				discoveredCount={2}
				pushState="Disabled"
				streamState="Idle"
				agentSuggestions={[]}
				selectedSuggestionIndex={0}
				slashCommands={[]}
				selectedSlashCommandIndex={0}
				fileSuggestions={[]} selectedFileSuggestionIndex={0} slashMenuVisible={false}
			/>
		);

		expect(app.lastFrame()).toContain("Ctrl+C");
		expect(app.lastFrame()).toContain("clears");
		expect(app.lastFrame()).toContain("Enter sends");
		expect(app.lastFrame()).toContain("@command-agent");
		expect(app.lastFrame()).toContain("Stream: Idle");
		expect(app.lastFrame()).toContain("Push:");
		expect(app.lastFrame()).toContain("Disabled");
		expect(app.lastFrame()).not.toContain("Discovered: 2");
		expect(app.lastFrame()).not.toContain("Ctrl+C exits");
		app.unmount();
	});

	it("positions the native terminal cursor at the logical draft cursor", async () => {
		const stdout = new TestTerminal();
		const stderr = new TestTerminal();
		const stdin = new PassThrough();
		const renderComposer = (
			draft: string,
			cursorOffset = draft.length
		): React.JSX.Element => (
			<ChatComposer
				draft={draft}
				cursorOffset={cursorOffset}
				activeAgentId="command-agent"
				discoveredCount={1}
				pushState="Disabled"
				streamState="Idle"
				agentSuggestions={[]}
				selectedSuggestionIndex={0}
				fileSuggestions={[]}
				selectedFileSuggestionIndex={0}
				slashCommands={[]}
				selectedSlashCommandIndex={0}
				slashMenuVisible={false}
			/>
		);
		const app = renderInk(renderComposer(""), {
			stdout: stdout as unknown as NodeJS.WriteStream,
			stderr: stderr as unknown as NodeJS.WriteStream,
			stdin: stdin as unknown as NodeJS.ReadStream,
			exitOnCtrlC: false,
			patchConsole: false,
			maxFps: 120
		});

		try {
			await vi.waitFor(() => {
				expect(stdout.output).toContain("\u001B[3G\u001B[?25h");
			});

			const outputLengthBeforeTyping = stdout.output.length;
			app.rerender(renderComposer("hello", 2));

			await vi.waitFor(() => {
				expect(stdout.output.slice(outputLengthBeforeTyping)).toContain(
					"\u001B[5G\u001B[?25h"
				);
			});

			const outputLengthBeforeMovingToEnd = stdout.output.length;
			app.rerender(renderComposer("hello"));

			await vi.waitFor(() => {
				expect(stdout.output.slice(outputLengthBeforeMovingToEnd)).toContain(
					"\u001B[8G\u001B[?25h"
				);
			});
		} finally {
			app.unmount();
			app.cleanup();
		}
	});

	it("moves the composer insertion point to a new row at the wrap boundary", () => {
		expect(wrapComposerDraft("1234", 4)).toEqual(["1234", ""]);
		expect(wrapComposerDraft("12345", 4)).toEqual(["1234", "5"]);
		expect(wrapComposerDraft("1234\n", 4)).toEqual(["1234", ""]);
	});

	it("renders the home screen discovery summary", () => {
		const app = render(
			<HomeScreen
				discoveredCount={2}
				terminalWidth={160}
			/>
		);

		expect(app.lastFrame()).toContain("2 agents discovered");
		expect(app.lastFrame()).toContain("Settings");
		expect(app.lastFrame()).toContain("Selected Agent");
		expect(app.lastFrame()).toContain("None");
		expect(app.lastFrame()).toContain("Request Mode");
		expect(app.lastFrame()).toContain("SendMessage");
		expect(app.lastFrame()).toContain("Response Mode");
		expect(app.lastFrame()).toContain("Message");
		expect(app.lastFrame()).toContain("Prefix Menus");
		expect(app.lastFrame()).toContain("/ Commands");
		expect(app.lastFrame()).toContain("@ Select Agent");
		expect(app.lastFrame()).toContain("# Attach File");
		app.unmount();
	});

	it("renders selected request and response modes in the home configuration panel", () => {
		const app = render(
			<HomeScreen
				discoveredCount={1}
				sourceCount={1}
				selectedAgentId="season-agent"
				requestMode="streaming-message"
				responseMode="a2a-protocol"
				terminalWidth={160}
			/>
		);

		expect(app.lastFrame()).toContain("@season-agent");
		expect(app.lastFrame()).toContain("SendStreamingMessage");
		expect(app.lastFrame()).toContain("A2A");
		app.unmount();
	});

	it("renders chat session entries below the inline home screen", () => {
		const app = render(
			<ChatSession
				entries={[
					{
						id: "agent-1",
						role: "agent",
						body: "agent reply",
						isFinalized: false
					}
				]}
				discoveredCount={2}
				sourceCount={1}
				selectedAgentId="season-agent"
				requestMode="send-message"
				responseMode="message-output"
			/>
		);

		expect(app.lastFrame()).toContain("2 agents discovered from 1 source");
		expect(app.lastFrame()).toContain("· agent reply");
		app.unmount();
	});

	it("moves finalized entries to scrollback while mutable output stays dynamic", () => {
		const userEntry = {
			id: "user-1",
			role: "user" as const,
			body: "question",
			isFinalized: true
		};
		const partialAgentEntry = {
			id: "agent-1",
			role: "agent" as const,
			body: "par",
			isFinalized: false
		};
		const renderSession = (entries: Array<typeof userEntry | typeof partialAgentEntry>) => (
			<ChatSession
				entries={entries}
				discoveredCount={1}
				sourceCount={1}
				selectedAgentId="season-agent"
				requestMode="streaming-message"
				responseMode="message-output"
			/>
		);
		const app = render(renderSession([userEntry, partialAgentEntry]));

		expect(app.frames.join("\n")).toContain("› question");
		expect(app.lastFrame()).toContain("· par");

		const frameCountAfterInitialRender = app.frames.length;
		const updatedAgentEntry = {
			...partialAgentEntry,
			body: "partial"
		};
		const changedFinalizedUserEntry = {
			...userEntry,
			body: "changed question"
		};
		app.rerender(renderSession([changedFinalizedUserEntry, updatedAgentEntry]));
		const updateFrames = app.frames.slice(frameCountAfterInitialRender).join("\n");

		expect(updateFrames).toContain("· partial");
		expect(updateFrames).not.toContain("changed question");

		const frameCountBeforeFinalization = app.frames.length;
		app.rerender(
			renderSession([
				changedFinalizedUserEntry,
				{ ...updatedAgentEntry, isFinalized: true }
			])
		);
		const finalizationFrames = app.frames
			.slice(frameCountBeforeFinalization)
			.join("\n");

		expect(finalizationFrames).toContain("· partial");
		app.unmount();
	});

	it("renders user messages with the composer-style chevron", () => {
		const app = render(
			<MessageBubble
				entry={{
					id: "user-1",
					role: "user",
					body: "hello there",
					isFinalized: true
				}}
				lineWidth={100}
			/>
		);

		expect(app.lastFrame()).toContain("› hello there");
		expect(app.lastFrame()).not.toContain("╭");
		expect(app.lastFrame()).not.toContain("You");
		app.unmount();
	});

	it("renders agent messages without the old bordered card", () => {
		const app = render(
			<MessageBubble
				entry={{
					id: "agent-1",
					role: "agent",
					body: "agent reply",
					isFinalized: true
				}}
				lineWidth={100}
			/>
		);

		expect(app.lastFrame()).toContain("· agent reply");
		expect(app.lastFrame()).not.toContain("╭");
		app.unmount();
	});

	it("renders muted transcript dividers", () => {
		const app = render(
			<MessageBubble
				entry={{
					id: "divider-1",
					role: "divider",
					body: "",
					isFinalized: true
				}}
				lineWidth={100}
			/>
		);

		expect(app.lastFrame()).toContain("--------");
		expect(app.lastFrame()).not.toContain("·");
		app.unmount();
	});

	it("renders system messages with a title-case label", () => {
		const app = render(
			<MessageBubble
				entry={{
					id: "system-1",
					role: "system",
					body: "connected",
					isFinalized: true
				}}
				lineWidth={100}
			/>
		);

		expect(app.lastFrame()).toContain("· System connected");
		expect(app.lastFrame()).not.toContain("╭");
		app.unmount();
	});

	it("renders transient system notifications outside chat session entries", () => {
		const app = render(
			<SystemNotificationStack
				notifications={[
					{
						id: "notification-1",
						role: "system",
						body: "Aion development registry: Auth failed.",
						isFinalized: true
					}
				]}
			/>
		);

		expect(app.lastFrame()).toContain(
			"· System Aion development registry: Auth failed."
		);
		app.unmount();
	});

	it("renders the working indicator with elapsed time", () => {
		const app = render(<WorkingIndicator startedAt={Date.now()} />);

		expect(app.lastFrame()).toContain("· Working");
		expect(app.lastFrame()).toContain("(0s)");
		app.unmount();
	});
});
