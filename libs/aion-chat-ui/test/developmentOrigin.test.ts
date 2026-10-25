import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";

import { parseCliArgs } from "../src/args.js";
import { accountName } from "../src/lib/credentialStore.js";
import { getAuthConfigUrl, getControlPlaneApiBaseUrl, getGraphQLHttpUrl, getGraphQLWebSocketUrl } from "../src/lib/environment.js";
import { buildAuthenticatedGraphQLWebSocketUrl } from "../src/lib/graphql/client.js";
import { loadChatSettings, saveSelectedEnvironment, selectChatEnvironment } from "../src/lib/chatSettings.js";
import { createDefaultLocalAgentSource, createDefaultRegistryAgentSource, mergeAgentSources } from "../src/lib/agents/model.js";
import { discoverAgentSources } from "../src/lib/agents/discovery.js";
import { SourceCredentials } from "../src/lib/sourceCredentials.js";
import { getStoredAccessToken } from "../src/lib/workosAuth.js";

const origin = "https://dev.example.test";

describe("explicit development origin", () => {
	it("accepts the optional URL in interactive, headless, and environment commands", () => {
		for (const args of [["--env", "development", origin], ["--env=development", origin], ["run", "--env", "development", origin, "hello"]]) {
			const command = parseCliArgs(args);
			expect(command.kind === "chat" || command.kind === "run" ? command.options : undefined)
				.toMatchObject({ environmentId: "development", controlPlaneOrigin: origin });
		}
		expect(parseCliArgs(["env", "development", origin])).toEqual({ kind: "environment", environmentId: "development", controlPlaneOrigin: origin });
		expect(parseCliArgs(["run", "--env", "development", "hello"])).toMatchObject({ options: { environmentId: "development", message: "hello" } });
		expect(parseCliArgs(["run", "--env", "development", "--", origin])).toMatchObject({ options: { message: origin } });
	});

	it.each(["ftp://dev.test", "https://user:secret@dev.test", "https://dev.test/api", "https://dev.test?token=secret", "https://dev.test#secret", "not-a-url", "https://dev.test/path/..", "https://dev.test/\\other.test", "https://dev.test\n"])("rejects invalid origin %s", (value) => {
		expect(() => parseCliArgs(["env", "development", value])).toThrow();
	});

	it("keeps hosted origins fixed and derives HTTP, auth and websocket URLs consistently", () => {
		expect(() => parseCliArgs(["--env", "production", origin])).toThrow(/only supported for development/);
		expect(() => getControlPlaneApiBaseUrl("staging", origin)).toThrow();
		expect(getControlPlaneApiBaseUrl("development", "https://DEV.example.test:443/")).toBe(origin);
		expect(getAuthConfigUrl("development", origin)).toBe(`${origin}/auth/cli/config`);
		expect(getGraphQLHttpUrl("development", origin)).toBe(`${origin}/api/graphql`);
		expect(getGraphQLWebSocketUrl("development", origin)).toBe("wss://dev.example.test/ws/graphql");
		expect(buildAuthenticatedGraphQLWebSocketUrl("development", "test-token", origin)).toBe("wss://dev.example.test/ws/graphql?token=test-token");
		expect(getControlPlaneApiBaseUrl("development")).toBe("http://localhost:8080");
	});

	it("persists the selection, reuses it without a URL, and permits an explicit reset", () => {
		const directory = mkdtempSync(path.join(tmpdir(), "chat-origin-"));
		try {
			const file = path.join(directory, "chat2.json");
			expect(saveSelectedEnvironment("development", file, `${origin}/`)).toBeUndefined();
			let settings = loadChatSettings(file).settings;
			expect(settings.environments.development.controlPlaneOrigin).toBe(origin);
			const key = createDefaultRegistryAgentSource("development").sourceKey;
			expect(settings.environments.development.agentSources[key].url).toBe(origin);
			saveSelectedEnvironment("production", file);
			saveSelectedEnvironment("development", file);
			settings = loadChatSettings(file).settings;
			expect(settings.environments.development.controlPlaneOrigin).toBe(origin);
			expect(settings.environments.production.controlPlaneOrigin).toBeUndefined();
			saveSelectedEnvironment("development", file, "http://localhost:8080");
			expect(loadChatSettings(file).settings.environments.development.agentSources[key].url).toBe("http://localhost:8080");
		} finally { rmSync(directory, { recursive: true, force: true }); }
	});

	it("replaces the existing registry source and retires its cached identities and context", () => {
		const settings = loadChatSettings("/nonexistent-chat-origin-test.json").settings;
		const old = createDefaultRegistryAgentSource("development");
		settings.environments.development.selectedAgentKey = "old";
		settings.environments.development.agents.old = { agentKey: "old", sourceKey: old.sourceKey, agentCardUrl: old.url, lastSeenAt: "2026-01-01", activeContextId: "old-context" };
		const selected = selectChatEnvironment(settings, "development", origin).environments.development;
		expect(selected.selectedAgentKey).toBeUndefined();
		expect(selected.agents).toEqual({});
		const sources = mergeAgentSources({ [old.sourceKey]: old }, "development", undefined, origin);
		expect(sources.filter((source) => source.type === "registry")).toEqual([expect.objectContaining({ sourceKey: old.sourceKey, url: origin })]);
	});

	it("uses custom-origin credentials for real registry discovery and still refuses a card on another origin", async () => {
		const source = createDefaultRegistryAgentSource("development", origin);
		const token = vi.fn(async () => "test-account");
		const fetcher = vi.fn<typeof fetch>(async (input, init) => {
			expect(String(input)).toBe(`${origin}/api/graphql`);
			expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer test-account");
			const { query } = JSON.parse(String(init?.body));
			if (query.includes("LoginBootstrap")) return Response.json({ data: { login: { nextRoute: null } } });
			if (query.includes("CurrentUser")) return Response.json({ data: { user: { id: "u", homeOrganization: { id: "org" }, agentIdentity: { id: "me", organizationId: "org", a2aUrl: null } } } });
			return Response.json({ data: { agentIdentityDetails: [] } });
		});
		const credentials = new SourceCredentials({ environmentId: "development", controlPlaneOrigin: origin, cli: { headers: {} }, accountToken: token, fetch: fetcher });
		try {
			const result = await discoverAgentSources([source], fetcher, { environmentId: "development", controlPlaneOrigin: origin, controlPlaneAccessTokenProvider: token, graphQLFetchImpl: credentials.fetch(source), sourceFetchImpl: (item) => credentials.fetch(item) });
			expect(result.errors).toEqual([]);
			expect(fetcher).toHaveBeenCalledTimes(3);
			await expect(credentials.fetch(source)("https://other.test/agent-card.json")).rejects.toThrow(/No credentials were sent/);
			expect(fetcher).toHaveBeenCalledTimes(3);
			const retiredFetch = credentials.fetch(source);
			credentials.cancelPending();
			await expect(retiredFetch(`${origin}/api/graphql`)).rejects.toThrow();
			expect(fetcher).toHaveBeenCalledTimes(3);
		} finally { credentials.dispose(); }
	});

	it("issues guests from the selected API and stores them in its own namespace", async () => {
		const keys: string[] = [];
		const fetcher = vi.fn<typeof fetch>(async (input) => String(input).endsWith("/auth/anonymous-sessions")
			? Response.json({ sessionId: "00000000-0000-4000-8000-000000000001", token: "guest-test", expiresAt: new Date(Date.now() + 86400000).toISOString() }) : new Response("ok"));
		const credentials = new SourceCredentials({ environmentId: "development", controlPlaneOrigin: origin, cli: { headers: {} }, accountToken: async () => undefined, fetch: fetcher, store: { getAnonymousSession: async () => undefined, setAnonymousSession: async (key) => { keys.push(key); } } });
		try {
			const source = createDefaultLocalAgentSource();
			await credentials.fetch(source)(`${source.url}/manifest.json`);
			expect(String(fetcher.mock.calls[0][0])).toBe(`${origin}/auth/anonymous-sessions`);
			expect(keys[0]).toContain(encodeURIComponent(origin));
		} finally { credentials.dispose(); }
	});

	it("partitions account cache and keychain keys by origin without reusing a previous login", async () => {
		const firstOrigin = "https://auth-a.test";
		const secondOrigin = "https://auth-b.test";
		const store = { getRefreshToken: vi.fn(async (_environment: string, selected?: string) => selected === firstOrigin ? "refresh-a" : undefined), setRefreshToken: vi.fn(async () => {}), deleteRefreshToken: vi.fn(async () => {}) };
		const fetcher = vi.fn<typeof fetch>(async (input) => String(input).endsWith("/auth/cli/config") ? Response.json({ apiBaseUrl: firstOrigin, authProvider: "workos", authMode: "cliDevice", workos: { clientId: "c", issuer: "https://auth.test", deviceAuthorizationUrl: "https://auth.test/device", tokenUrl: "https://auth.test/token", refreshTokenUrl: "https://auth.test/token", supportsDirectRefresh: true } }) : Response.json({ access_token: "access-a", refresh_token: "rotated-a", expires_in: 3600 }));
		expect(await getStoredAccessToken("development", { controlPlaneOrigin: firstOrigin, credentialStore: store, fetchImpl: fetcher })).toBe("access-a");
		expect(await getStoredAccessToken("development", { controlPlaneOrigin: secondOrigin, credentialStore: store, fetchImpl: fetcher })).toBeUndefined();
		expect(store.getRefreshToken).toHaveBeenLastCalledWith("development", secondOrigin);
		expect(store.setRefreshToken).toHaveBeenCalledWith("development", "rotated-a", firstOrigin);
		expect(String(fetcher.mock.calls[0][0])).toBe(`${firstOrigin}/auth/cli/config`);
		expect(fetcher).toHaveBeenCalledTimes(2);
		expect(accountName("development")).toBe("development:user");
		expect(accountName("development", "http://localhost:8080/")).toBe("development:user");
		expect(accountName("development", firstOrigin)).toBe("development:user:https%3A%2F%2Fauth-a.test");
	});
});
