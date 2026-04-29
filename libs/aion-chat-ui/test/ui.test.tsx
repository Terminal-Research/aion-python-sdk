import React from "react";
import { describe, expect, it } from "vitest";
import { render } from "ink-testing-library";

import { ChatComposer } from "../src/components/ChatComposer.js";
import { HomeScreen } from "../src/components/HomeScreen.js";
import {
	MessageBubble,
	WorkingIndicator
} from "../src/components/MessageBubble.js";

describe("Ink components", () => {
	it("renders the agent picker and hides the footer while the @ menu is open", () => {
		const app = render(
			<ChatComposer
				draft=""
				activeAgentId={undefined}
				discoveredCount={2}
				pushState="Disabled"
				streamState="Idle"
				agentSuggestions={["command-agent", "openrouter-chat"]}
				selectedSuggestionIndex={0}
				slashCommands={[]}
				selectedSlashCommandIndex={0}
				fileSuggestions={[]} selectedFileSuggestionIndex={0} slashMenuVisible={false}
			/>
		);

		expect(app.lastFrame()).toContain("Send message");
		expect(app.lastFrame()).toContain("Discovered: 2");
		expect(app.lastFrame()).toContain("@command-agent");
		expect(app.lastFrame()).not.toContain("Stream: Idle");
		expect(app.lastFrame()).not.toContain("Push:");
		expect(app.lastFrame()).not.toContain("Ctrl+C");
		app.unmount();
	});

	it("renders the slash command list and hides the footer while it is open", () => {
		const app = render(
			<ChatComposer
				draft="/re"
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
				activeAgentId="command-agent"
				discoveredCount={2}
				pushState="Disabled"
				streamState="Idle"
				agentSuggestions={[]}
				selectedSuggestionIndex={0}
				slashCommands={[
					{
						label: "/clear",
						description: "Clear the visible transcript and start fresh."
					}
				]}
				selectedSlashCommandIndex={0}
				fileSuggestions={[]} selectedFileSuggestionIndex={0} slashMenuVisible={true}
			/>
		);

		expect(app.lastFrame()).toContain("/clear");
		expect(app.lastFrame()).toContain("Clear the visible transcript and start fresh.");
		expect(app.lastFrame()).not.toContain("Ctrl+C");
		app.unmount();
	});

	it("renders the slash submenu inside the composer", () => {
		const app = render(
			<ChatComposer
				draft=""
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

	it("renders user messages with the composer-style chevron", () => {
		const app = render(
			<MessageBubble entry={{ id: "user-1", role: "user", body: "hello there" }} />
		);

		expect(app.lastFrame()).toContain("› hello there");
		expect(app.lastFrame()).not.toContain("╭");
		expect(app.lastFrame()).not.toContain("You");
		app.unmount();
	});

	it("renders agent messages without the old bordered card", () => {
		const app = render(
			<MessageBubble entry={{ id: "agent-1", role: "agent", body: "agent reply" }} />
		);

		expect(app.lastFrame()).toContain("· agent reply");
		expect(app.lastFrame()).not.toContain("╭");
		app.unmount();
	});

	it("renders system messages with a title-case label", () => {
		const app = render(
			<MessageBubble entry={{ id: "system-1", role: "system", body: "connected" }} />
		);

		expect(app.lastFrame()).toContain("· System connected");
		expect(app.lastFrame()).not.toContain("╭");
		app.unmount();
	});

	it("renders the working indicator with elapsed time", () => {
		const app = render(<WorkingIndicator startedAt={Date.now()} />);

		expect(app.lastFrame()).toContain("· Working");
		expect(app.lastFrame()).toContain("(0s)");
		app.unmount();
	});
});
