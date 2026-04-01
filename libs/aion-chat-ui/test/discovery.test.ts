import { describe, expect, it, vi } from "vitest";

import { discoverAgents, getManifestUrl } from "../src/lib/discovery.js";

describe("discovery", () => {
	it("derives the manifest URL from the proxy root", () => {
		expect(getManifestUrl("http://localhost:8000")).toBe(
			"http://localhost:8000/.well-known/manifest.json"
		);
	});

	it("strips proxy agent paths when building the manifest URL", () => {
		expect(getManifestUrl("http://localhost:8000/agents/command-agent")).toBe(
			"http://localhost:8000/.well-known/manifest.json"
		);
	});

	it("discovers and sorts agents from the manifest", async () => {
		const fetchImpl = vi.fn(async () =>
			new Response(
				JSON.stringify({
					endpoints: {
						"z-agent": "/agents/z-agent",
						"a-agent": "/agents/a-agent"
					}
				}),
				{ status: 200 }
			)
		);

		await expect(discoverAgents("http://localhost:8000", fetchImpl)).resolves.toEqual({
			rootUrl: "http://localhost:8000",
			manifestUrl: "http://localhost:8000/.well-known/manifest.json",
			agents: [
				{ id: "a-agent", path: "/agents/a-agent" },
				{ id: "z-agent", path: "/agents/z-agent" }
			]
		});
	});
});
