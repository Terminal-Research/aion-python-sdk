import React from "react";
import { describe, expect, it } from "vitest";
import { render } from "ink-testing-library";

import { ChatComposer } from "../src/components/ChatComposer.js";
import { HomeScreen } from "../src/components/HomeScreen.js";

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
		expect(app.lastFrame()).toContain("Type @ to select an agent");
		app.unmount();
	});
});
