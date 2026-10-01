import { describe, expect, it, vi } from "vitest";
import { SourceCredentials } from "../src/lib/sourceCredentials.js";
import { createDefaultLocalAgentSource, createDefaultRegistryAgentSource, createExplicitAgentSource } from "../src/lib/agents/model.js";

const local = createDefaultLocalAgentSource();
const registry = createDefaultRegistryAgentSource("development");
const guest = { sessionId: "00000000-0000-4000-8000-000000000001", token: "guest-secret", expiresAt: new Date(Date.now() + 30 * 86400000).toISOString() };
function setup() {
	const fetcher = vi.fn<typeof fetch>(async (input) => String(input).endsWith("/auth/anonymous-sessions") ? Response.json(guest) : Response.json({ ok: true }));
	const account = vi.fn(async (): Promise<string | undefined> => "account-secret");
	const credentials = new SourceCredentials({ environmentId: "development", cli: { headers: {} }, accountToken: account, fetch: fetcher,
		store: { getAnonymousSession: async () => undefined, setAnonymousSession: async () => undefined } });
	return { credentials, fetcher, account };
}

describe("source-aware credentials", () => {
	it.each(["object", "headers", "request"])("merges operation extensions with explicit headers supplied as %s", async (inputKind) => {
		const { fetcher, account } = setup();
		const source = createExplicitAgentSource("http://localhost:9000");
		const credentials = new SourceCredentials({ environmentId: "development",
			cli: { token: "explicit", headers: {
				"a2a-EXTENSIONS": " shared, configured, , shared ",
				"X-Test": "configured", Authorization: "Bearer header-token"
			} }, accountToken: account, fetch: fetcher });
		const headers = { "A2A-Extensions": "request, shared, request",
			"X-Test": "request", Authorization: "Bearer request-token" };
		const send = credentials.fetch(source);
		if (inputKind === "request") await send(new Request(source.url, { headers }));
		else await send(source.url, { headers: inputKind === "headers" ? new Headers(headers) : headers });
		const sent = new Headers(fetcher.mock.lastCall?.[1]?.headers);
		expect(sent.get("A2A-Extensions")).toBe("request,shared,configured");
		expect(sent.get("X-Test")).toBe("configured");
		expect(sent.get("Authorization")).toBe("Bearer explicit");
		expect(account).not.toHaveBeenCalled();
		expect(fetcher).toHaveBeenCalledTimes(1);
	});

	it("does not let a retired account lookup overwrite the new local-history owner", async () => {
		const { credentials, fetcher, account } = setup();
		let finishOld!: (token: string) => void;
		account.mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve; }));
		const pending = credentials.fetch(registry)(registry.url);
		const cancelled = expect(pending).rejects.toThrow();
		credentials.cancelPending();
		account.mockResolvedValue("new-account");
		await credentials.fetch(registry)(registry.url);
		const owner = credentials.owner(registry);
		finishOld("old-account");
		await cancelled;
		expect(credentials.owner(registry)).toBe(owner);
		expect(fetcher).toHaveBeenCalledTimes(1);
	});

	it("supports guest credentials at a public control-plane agent route without making catalog discovery public", async () => {
		const { credentials, fetcher, account } = setup();
		const publicAgent = { ...registry, type: "agentCard" as const, url: registry.url + "/distributions/public/a2a" };
		await credentials.fetch(publicAgent)(publicAgent.url);
		expect(new Headers(fetcher.mock.lastCall?.[1]?.headers).get("Authorization")).toBe("Bearer guest-secret");
		expect(account).not.toHaveBeenCalled();
	});

	it("does not send an uncredentialed agent request when session issuance fails", async () => {
		const { credentials, fetcher } = setup();
		fetcher.mockResolvedValue(new Response(null, { status: 503 }));
		await expect(credentials.fetch(local)(local.url)).rejects.toMatchObject({ reason: "temporary" });
		expect(fetcher).toHaveBeenCalledTimes(1);
		expect(String(fetcher.mock.lastCall?.[0])).toContain("/auth/anonymous-sessions");
	});
	it("uses guests for all direct methods and fresh account credentials only at the matching registry", async () => {
		const { credentials, fetcher, account } = setup();
		const direct = credentials.fetch(local);
		for (const path of ["/.well-known/manifest.json", "/agents/demo/.well-known/agent-card.json", "/tasks/1?historyLength=1", "/contexts/1", "/stream"]) {
			await direct(local.url + path);
			expect(new Headers(fetcher.mock.lastCall?.[1]?.headers).get("Authorization")).toBe("Bearer guest-secret");
		}
		expect(account).not.toHaveBeenCalled();
		await credentials.fetch(registry)(registry.url + "/distributions/demo/a2a");
		expect(new Headers(fetcher.mock.lastCall?.[1]?.headers).get("Authorization")).toBe("Bearer account-secret");
		account.mockResolvedValue("refreshed-account");
		await credentials.fetch(registry)(registry.url + "/distributions/demo/a2a");
		expect(new Headers(fetcher.mock.lastCall?.[1]?.headers).get("Authorization")).toBe("Bearer refreshed-account");
	});

	it("blocks redirects and card/manifest pivots before acquiring or sending credentials", async () => {
		const { credentials, fetcher, account } = setup();
		await expect(credentials.fetch(local)("https://evil.test/card")).rejects.toThrow("Untrusted");
		expect(fetcher).not.toHaveBeenCalled(); expect(account).not.toHaveBeenCalled();
		await credentials.fetch(local)(local.url);
		expect(fetcher.mock.lastCall?.[1]).toMatchObject({ redirect: "error", credentials: "omit" });
	});

	it("never converts an account auth failure into guest mode", async () => {
		const { credentials, fetcher, account } = setup();
		account.mockRejectedValue(new Error("login required"));
		await expect(credentials.fetch(registry)(registry.url)).rejects.toThrow("login required");
		expect(fetcher).not.toHaveBeenCalled();
		account.mockResolvedValue(undefined);
		await expect(credentials.fetch(registry)(registry.url)).rejects.toThrow("requires an account");
		expect(fetcher).not.toHaveBeenCalled();
	});

	it("refuses unknown registries without consulting the account provider", async () => {
		const { credentials, fetcher, account } = setup();
		const source = { ...registry, url: "https://other.test" };
		await expect(credentials.fetch(source)(source.url)).rejects.toThrow("restricted");
		expect(account).not.toHaveBeenCalled(); expect(fetcher).not.toHaveBeenCalled();
	});

	it("reuses a held guest after login lifecycle cancellation but rejects stale requests", async () => {
		const { credentials, fetcher } = setup();
		const old = credentials.fetch(local);
		await old(local.url);
		credentials.cancelPending();
		await expect(old(local.url)).rejects.toThrow();
		await credentials.fetch(local)(local.url);
		expect(fetcher.mock.calls.filter(([input]) => String(input).endsWith("/auth/anonymous-sessions"))).toHaveLength(1);
	});

	it("turns receiver rejection into an explicit new-session action, not replay", async () => {
		const { credentials, fetcher } = setup();
		await credentials.fetch(local)(local.url);
		fetcher.mockResolvedValue(new Response(null, { status: 401 }));
		await expect(credentials.fetch(local)(local.url)).rejects.toMatchObject({ reason: "rejected" });
		const count = fetcher.mock.calls.length;
		await expect(credentials.fetch(local)(local.url)).rejects.toThrow("previous session's conversations");
		expect(fetcher).toHaveBeenCalledTimes(count);
	});

	it("preserves explicit endpoint credentials without forwarding them to other sources", async () => {
		const { fetcher, account } = setup();
		const credentials = new SourceCredentials({ environmentId: "development", cli: { token: "explicit", headers: { "X-Test": "explicit-only", "A2A-Extensions": "explicit-extension" } }, accountToken: account, fetch: fetcher });
		const source = createExplicitAgentSource("http://localhost:9000");
		await credentials.fetch(source)(source.url);
		expect(new Headers(fetcher.mock.lastCall?.[1]?.headers).get("Authorization")).toBe("Bearer explicit");
		await credentials.fetch(registry)(registry.url);
		expect(new Headers(fetcher.mock.lastCall?.[1]?.headers).has("X-Test")).toBe(false);
		expect(new Headers(fetcher.mock.lastCall?.[1]?.headers).has("A2A-Extensions")).toBe(false);
	});
});
