import React from "react";
import { describe, expect, it } from "vitest";
import { render } from "ink-testing-library";

import { ChatComposer } from "../src/components/ChatComposer.js";
import { HomeScreen } from "../src/components/HomeScreen.js";
import { StatusBar } from "../src/components/StatusBar.js";

describe("Ink components", () => {
	it("renders the chat composer placeholder", () => {
		const app = render(
			<ChatComposer
				connected={true}
				draft=""
				activeAgentId={undefined}
				agentSuggestions={["command-agent", "openrouter-chat"]}
				selectedSuggestionIndex={0}
			/>
		);

		expect(app.lastFrame()).toContain("Type @ to choose an agent");
		expect(app.lastFrame()).toContain("@command-agent");
		expect(app.lastFrame()).toContain("Ctrl+C exits");
		expect(app.lastFrame()).not.toContain("Tab completes agent");
		app.unmount();
	});

	it("renders the composer clear hint when draft content exists", () => {
		const app = render(
			<ChatComposer
				connected={true}
				draft="hello"
				activeAgentId="command-agent"
				agentSuggestions={[]}
				selectedSuggestionIndex={0}
			/>
		);

		expect(app.lastFrame()).toContain("Ctrl+C clears content");
		expect(app.lastFrame()).not.toContain("Ctrl+C exits");
		app.unmount();
	});

	it("renders the status bar values", () => {
		const app = render(
			<StatusBar
				connectionState="Connected"
				pushState="Disabled"
				streamState="Idle"
				discoveredAgents={2}
				activeAgentId="command-agent"
			/>
		);

		expect(app.lastFrame()).toContain("Connection: Connected");
		expect(app.lastFrame()).toContain("Push: Disabled");
		expect(app.lastFrame()).toContain("Agent: @command-agent");
		app.unmount();
	});

	it("renders the home screen discovery state", () => {
		const app = render(
			<HomeScreen
				discoveredCount={2}
				discoveryState="Discovered 2 agents from the proxy manifest"
			/>
		);

		expect(app.lastFrame()).toContain("2 agents discovered");
		expect(app.lastFrame()).toContain("Type @ to select an agent");
		app.unmount();
	});
});
