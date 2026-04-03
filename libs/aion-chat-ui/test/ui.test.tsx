import React from "react";
import { describe, expect, it } from "vitest";
import { render } from "ink-testing-library";

import { ChatComposer } from "../src/components/ChatComposer.js";
import { HomeScreen } from "../src/components/HomeScreen.js";

describe("Ink components", () => {
	it("renders the chat composer placeholder", () => {
		const app = render(
			<ChatComposer
				connected={true}
				draft=""
				activeAgentId={undefined}
				discoveredCount={2}
				pushState="Disabled"
				streamState="Idle"
				agentSuggestions={["command-agent", "openrouter-chat"]}
				selectedSuggestionIndex={0}
			/>
		);

		expect(app.lastFrame()).toContain("Send message");
		expect(app.lastFrame()).toContain("Discovered: 2");
		expect(app.lastFrame()).toContain("@command-agent");
		expect(app.lastFrame()).not.toContain("Stream: Idle");
		expect(app.lastFrame()).not.toContain("Push:");
		expect(app.lastFrame()).not.toContain("Ctrl+C");
		expect(app.lastFrame()).not.toContain("Composer");
		app.unmount();
	});

	it("renders the composer clear hint when draft content exists", () => {
		const app = render(
			<ChatComposer
				connected={true}
				draft="hello"
				activeAgentId="command-agent"
				discoveredCount={2}
				pushState="Disabled"
				streamState="Idle"
				agentSuggestions={[]}
				selectedSuggestionIndex={0}
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

	it("renders the home screen discovery state", () => {
		const app = render(
			<HomeScreen
				discoveredCount={2}
				discoveryState="Discovered 2 agents from the proxy manifest"
				terminalWidth={160}
			/>
		);

		expect(app.lastFrame()).toContain("2 agents discovered");
		expect(app.lastFrame()).toContain("Type @ to select an agent");
		app.unmount();
	});
});
