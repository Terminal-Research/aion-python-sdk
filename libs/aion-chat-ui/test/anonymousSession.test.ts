import { describe, expect, it, vi } from "vitest";
import { AnonymousSession, anonymousSessionKey, type AnonymousSessionStore } from "../src/lib/anonymousSession.js";

const now = Date.parse("2026-09-30T12:00:00Z");
const day = 86_400_000;
const scope = { controlPlaneUrl: "https://api.example.test", environmentId: "development", sourceId: "local", destinationOrigin: "http://localhost:8000" };
const initial = { version: 1, sessionId: "00000000-0000-4000-8000-000000000001", token: "guest-secret", expiresAt: new Date(now + 30 * day).toISOString() };
function store(value?: string): AnonymousSessionStore {
	return { getAnonymousSession: vi.fn(async () => value), setAnonymousSession: vi.fn(async (_key, next) => { value = next; }) };
}
function setup(value?: object) {
	let clock = now;
	const storage = store(value ? JSON.stringify(value) : undefined);
	const fetcher = vi.fn<typeof fetch>(async () => Response.json(initial));
	const session = new AnonymousSession({ ...scope, store: storage, fetch: fetcher, now: () => clock });
	return { session, storage, fetcher, advance: (ms: number) => { clock += ms; } };
}

describe("anonymous session lifecycle", () => {
	it("separates every scope dimension and canonicalizes URL spellings", () => {
		const key = anonymousSessionKey(scope);
		expect(anonymousSessionKey({ ...scope, controlPlaneUrl: "https://API.example.test:443/" })).toBe(key);
		for (const update of [{ sourceId: "other" }, { environmentId: "production" }, { destinationOrigin: "http://localhost:9000" }, { controlPlaneUrl: "https://api.example.test/base" }]) {
			expect(anonymousSessionKey({ ...scope, ...update })).not.toBe(key);
		}
		expect(() => anonymousSessionKey({ ...scope, controlPlaneUrl: "https://u:p@example.test" })).toThrow();
	});

	it("deduplicates issuance, persists separately and restores without network", async () => {
		const { session, storage, fetcher } = setup();
		expect(await Promise.all([session.token(), session.token()])).toEqual([initial.token, initial.token]);
		expect(fetcher).toHaveBeenCalledTimes(1);
		expect(fetcher.mock.calls[0][1]).toMatchObject({ method: "POST", headers: {}, credentials: "omit", redirect: "error" });
		const restored = new AnonymousSession({ ...scope, store: storage, fetch: fetcher, now: () => now });
		expect(await restored.token()).toBe(initial.token);
		expect(fetcher).toHaveBeenCalledTimes(1);
	});

	it("renews the same identity during active use and uses the renewed bearer", async () => {
		const { session, fetcher } = setup({ ...initial, expiresAt: new Date(now + day).toISOString() });
		fetcher.mockResolvedValue(Response.json({ ...initial, token: "renewed" }));
		expect(await session.token()).toBe("renewed");
		expect(fetcher.mock.calls[0][1]?.headers).toEqual({ Authorization: `Bearer ${initial.token}` });
		expect(session.owner).toBe(initial.sessionId);
	});

	it("does not accept renewal that changes identity", async () => {
		const { session, fetcher } = setup({ ...initial, expiresAt: new Date(now + day).toISOString() });
		fetcher.mockResolvedValue(Response.json({ ...initial, sessionId: "00000000-0000-4000-8000-000000000002" }));
		expect(await session.token()).toBe(initial.token);
		expect(session.owner).toBe(initial.sessionId);
	});

	it("retains a valid token through transient failure with bounded active-use backoff", async () => {
		const { session, fetcher, advance } = setup({ ...initial, expiresAt: new Date(now + day).toISOString() });
		fetcher.mockImplementation(async () => { throw new Error("network contains secret"); });
		for (const delay of [1000, 5000, 30000, 300000]) {
			await expect(session.token()).resolves.toBe(initial.token);
			const count = fetcher.mock.calls.length;
			advance(delay - 1);
			await session.token();
			expect(fetcher).toHaveBeenCalledTimes(count);
			advance(1);
		}
		expect(fetcher).toHaveBeenCalledTimes(4);
	});

	it.each(["120", new Date(now + 120_000).toUTCString(), "malformed"])("honors Retry-After %s even on manual retry", async (header) => {
		const { session, fetcher, advance } = setup();
		fetcher.mockImplementation(async () => new Response(null, { status: 429, headers: { "Retry-After": header } }));
		await expect(session.token()).rejects.toMatchObject({ reason: "temporary" });
		advance(1000);
		await expect(session.retry()).rejects.toMatchObject({ reason: "temporary" });
		expect(fetcher).toHaveBeenCalledTimes(1);
		advance(120_000);
		await expect(session.retry()).rejects.toMatchObject({ reason: "temporary" });
		expect(fetcher).toHaveBeenCalledTimes(2);
	});

	it.each(["expired", "rejected"])("requires explicit replacement after %s, with a history warning", async (reason) => {
		const { session, fetcher } = setup({ ...initial, expiresAt: new Date(now + (reason === "expired" ? -day : day)).toISOString() });
		if (reason === "rejected") fetcher.mockImplementation(async () => new Response(null, { status: 401 }));
		await expect(session.token()).rejects.toMatchObject({ reason });
		await expect(session.token()).rejects.toThrow("previous session's conversations");
		fetcher.mockResolvedValue(Response.json(initial));
		await session.startNew();
		expect(fetcher.mock.lastCall?.[1]?.headers).toEqual({});
	});

	it("handles storage loss without discarding a valid in-memory credential", async () => {
		const { session, storage, fetcher } = setup();
		vi.mocked(storage.getAnonymousSession).mockRejectedValue(new Error("keychain unavailable"));
		vi.mocked(storage.setAnonymousSession).mockRejectedValue(new Error("keychain unavailable"));
		expect(await session.token()).toBe(initial.token);
		session.cancelPending();
		expect(await session.token()).toBe(initial.token);
		expect(fetcher).toHaveBeenCalledTimes(1);
	});

	it("ignores an exchange completing after disposal and never stores it", async () => {
		const { session, fetcher, storage } = setup();
		let complete!: (response: Response) => void;
		fetcher.mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
		const pending = session.token();
		await vi.waitFor(() => expect(fetcher).toHaveBeenCalled());
		session.dispose(); complete(Response.json(initial));
		await expect(pending).rejects.toThrow();
		expect(storage.setAnonymousSession).not.toHaveBeenCalled();
	});
});
