/** Persistent guest credentials are separate from account refresh tokens. */
export interface AnonymousSessionStore {
	getAnonymousSession(key: string): Promise<string | undefined>;
	setAnonymousSession(key: string, value: string): Promise<void>;
}

export interface AnonymousSessionScope {
	controlPlaneUrl: string;
	environmentId: string;
	sourceId: string;
	destinationOrigin: string;
}

interface Session {
	version: 1;
	sessionId: string;
	token: string;
	expiresAt: string;
}

export const GUEST_HISTORY_WARNING =
	"A new guest session will not have access to the previous session's conversations.";

export class GuestSessionError extends Error {
	constructor(readonly reason: "expired" | "rejected" | "temporary") {
		super(reason === "temporary"
			? "Guest session temporarily unavailable. Retry later; your identity has not been replaced."
			: `Your guest session has ${reason === "expired" ? "expired" : "been rejected"}. Use /session new <source-key> (or --new-session with --url). ${GUEST_HISTORY_WARNING}`);
		this.name = "GuestSessionError";
	}
}

/** Configuration, not a discovered card or token claim, defines trust. */
export function trustedUrl(value: string): URL {
	const url = new URL(value);
	if (url.username || url.password || url.search || url.hash ||
		!["http:", "https:"].includes(url.protocol)) {
		throw new Error("Credential scope requires an absolute HTTP(S) URL.");
	}
	return url;
}

export function anonymousSessionKey(scope: AnonymousSessionScope): string {
	if (!scope.environmentId || !scope.sourceId) throw new Error("Missing session scope.");
	return "aion-chat:anonymous-session:v1:" + [
		trustedUrl(scope.controlPlaneUrl).href.replace(/\/$/, ""),
		scope.environmentId, scope.sourceId,
		trustedUrl(scope.destinationOrigin).origin
	].map(encodeURIComponent).join(":");
}

function parseSession(value: unknown): Session | undefined {
	if (!value || typeof value !== "object") return undefined;
	const data = value as Partial<Session>;
	if (data.version !== 1 || typeof data.sessionId !== "string" ||
		!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(data.sessionId) ||
		typeof data.token !== "string" || !data.token || /\s/.test(data.token) || data.token.length > 8192 ||
		typeof data.expiresAt !== "string" || !data.expiresAt.endsWith("Z") ||
		!Number.isFinite(Date.parse(data.expiresAt))) return undefined;
	return data as Session;
}

/**
 * One source's guest lifecycle. Renew only during active use, deduplicate work,
 * and retain a valid bearer through transient outages or keychain failure.
 * Expiry/rejection requires explicit replacement, never an automatic new owner.
 */
export class AnonymousSession {
	readonly key: string;
	private session?: Session;
	private restored?: Promise<void>;
	private pending?: Promise<void>;
	private writes: Promise<void> = Promise.resolve();
	private lifetime = new AbortController();
	private failures = 0;
	private nextAttemptAt = 0;
	private retryAfterAt = 0;
	private rejected = false;
	private readonly now: () => number;
	private readonly fetcher: typeof fetch;

	constructor(private readonly options: AnonymousSessionScope & {
		store: AnonymousSessionStore;
		fetch?: typeof fetch;
		now?: () => number;
	}) {
		this.key = anonymousSessionKey(options);
		this.now = options.now ?? Date.now;
		this.fetcher = options.fetch ?? fetch;
	}

	get signal(): AbortSignal { return this.lifetime.signal; }
	get owner(): string | undefined { return this.session?.sessionId; }

	private async restore(): Promise<void> {
		const signal = this.signal;
		this.restored ??= (async () => {
			try {
				const value = await this.options.store.getAnonymousSession(this.key);
				signal.throwIfAborted();
				this.session = value ? parseSession(JSON.parse(value)) : undefined;
			} catch { /* Storage loss is ordinary initialization, not expiry. */ }
		})();
		await this.restored;
		signal.throwIfAborted();
	}

	async token(): Promise<string> {
		const signal = this.signal;
		await this.restore();
		if (this.session && Date.parse(this.session.expiresAt) <= this.now()) {
			throw new GuestSessionError("expired");
		}
		if (this.rejected) throw new GuestSessionError("rejected");
		if (!this.session || Date.parse(this.session.expiresAt) - this.now() < 7 * 86_400_000) {
			if (this.now() >= Math.max(this.nextAttemptAt, this.retryAfterAt)) {
				if (!this.pending) {
					const pending = this.exchange();
					this.pending = pending;
					void pending.finally(() => {
						if (this.pending === pending) this.pending = undefined;
					}).catch(() => undefined);
				}
				await this.pending;
			}
		}
		signal.throwIfAborted();
		if (!this.session) throw new GuestSessionError("temporary");
		if (this.rejected) throw new GuestSessionError("rejected");
		if (Date.parse(this.session.expiresAt) <= this.now()) throw new GuestSessionError("expired");
		return this.session.token;
	}

	/** A receiver's 401 cannot cause transparent identity replacement. */
	reject(): void { this.rejected = true; }

	async retry(): Promise<void> {
		this.failures = 0;
		this.nextAttemptAt = 0;
		await this.token(); // A manual retry still honors Retry-After.
	}

	async startNew(): Promise<void> {
		await this.restore();
		this.cancelPending();
		this.session = undefined;
		this.rejected = false;
		this.failures = 0;
		this.nextAttemptAt = 0;
		await this.token();
	}

	dispose(): void { this.lifetime.abort(); }

	/** Cancel stale work without losing a bearer already held in memory. */
	cancelPending(): void {
		this.lifetime.abort();
		this.lifetime = new AbortController();
		this.pending = undefined;
		this.restored = this.session ? Promise.resolve() : undefined;
	}

	private async exchange(): Promise<void> {
		const signal = this.signal;
		const previous = this.session;
		const endpoint = trustedUrl(this.options.controlPlaneUrl);
		endpoint.pathname = endpoint.pathname.replace(/\/$/, "") + "/auth/anonymous-sessions";
		const timeout = new AbortController();
		const timer = setTimeout(() => timeout.abort(), 10_000);
		try {
			const response = await this.fetcher(endpoint, {
				method: "POST", credentials: "omit", redirect: "error",
				headers: previous ? { Authorization: `Bearer ${previous.token}` } : {},
				signal: AbortSignal.any([signal, timeout.signal])
			});
			signal.throwIfAborted();
			if (response.status === 429) {
				const value = response.headers.get("Retry-After") ?? "";
				const deadline = /^\d+$/.test(value) ? this.now() + Number(value) * 1000 : Date.parse(value);
				this.retryAfterAt = Number.isFinite(deadline) ? Math.max(this.now(), deadline) : this.now() + 60_000;
			}
			if (!response.ok) {
				await response.body?.cancel();
				signal.throwIfAborted();
				if (response.status === 401 && previous) this.rejected = true;
				if (response.status >= 400 && response.status < 500 && response.status !== 429) {
					this.nextAttemptAt = Infinity;
				}
				throw new GuestSessionError("temporary");
			}
			const data: unknown = await response.json();
			signal.throwIfAborted();
			const next = parseSession({ ...(data as object), version: 1 });
			if (!next || Date.parse(next.expiresAt) <= this.now() ||
				(previous && previous.sessionId !== next.sessionId)) {
				throw new GuestSessionError("temporary");
			}
			this.session = next;
			this.failures = 0;
			this.nextAttemptAt = 0;
			this.retryAfterAt = 0;
			// Keep writes ordered across replacement so an old keychain write cannot
			// finish after, and overwrite, the newly issued identity.
			this.writes = this.writes.then(async () => {
				if (!signal.aborted) {
					await this.options.store.setAnonymousSession(this.key, JSON.stringify(next));
				}
			}).catch(() => { /* No plaintext fallback; retain the in-memory bearer. */ });
			await this.writes;
		} catch {
			signal.throwIfAborted();
			if (this.nextAttemptAt !== Infinity) {
				this.nextAttemptAt = this.now() + ([1000, 5000, 30000][this.failures] ?? 300_000);
				this.failures = this.failures >= 3 ? 0 : this.failures + 1;
			}
		} finally { clearTimeout(timer); }
	}
}
