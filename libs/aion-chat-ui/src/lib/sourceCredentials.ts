import type { ChatCliOptions } from "../args.js";
import { AnonymousSession, trustedUrl, type AnonymousSessionStore } from "./anonymousSession.js";
import { defaultCredentialStore } from "./credentialStore.js";
import { getControlPlaneApiBaseUrl, type AionEnvironmentId } from "./environment.js";
import { hashValue, isTransientAgentSource, normalizeSourceUrl, type RuntimeAgentSource } from "./agents/model.js";

/** Shared by discovery, interactive connections and headless requests. */
export class SourceCredentials {
	private readonly guests = new Map<string, AnonymousSession>();
	private readonly owners = new Map<string, string>();
	private lifetime = new AbortController();
	private readonly fetcher: typeof fetch;

	constructor(private readonly options: {
		environmentId: AionEnvironmentId;
		cli: Pick<ChatCliOptions, "token" | "headers">;
		accountToken: () => Promise<string | undefined>;
		store?: AnonymousSessionStore;
		fetch?: typeof fetch;
	}) { this.fetcher = options.fetch ?? fetch; }

	get signal(): AbortSignal { return this.lifetime.signal; }

	isRegistry(source: RuntimeAgentSource): boolean {
		return source.type === "registry" && normalizeSourceUrl(source.url) ===
			normalizeSourceUrl(getControlPlaneApiBaseUrl(this.options.environmentId));
	}

	private explicitHeaders(source: RuntimeAgentSource): Headers {
		const headers = new Headers(isTransientAgentSource(source) ? this.options.cli.headers : {});
		if (isTransientAgentSource(source) && this.options.cli.token) {
			headers.set("Authorization", `Bearer ${this.options.cli.token}`);
		}
		return headers;
	}

	private guest(source: RuntimeAgentSource): AnonymousSession {
		if (source.type === "registry" || this.explicitHeaders(source).has("Authorization")) {
			throw new Error("This source does not use a guest session.");
		}
		const key = `${source.sourceKey}:${trustedUrl(source.url).origin}`;
		let guest = this.guests.get(key);
		if (!guest) {
			guest = new AnonymousSession({
				controlPlaneUrl: getControlPlaneApiBaseUrl(this.options.environmentId),
				environmentId: this.options.environmentId, sourceId: source.sourceKey,
				destinationOrigin: trustedUrl(source.url).origin,
				store: this.options.store ?? defaultCredentialStore, fetch: this.fetcher
			});
			this.guests.set(key, guest);
		}
		return guest;
	}

	/** Non-secret cache namespace, never used as an authorization assertion. */
	owner(source: RuntimeAgentSource): string | undefined { return this.owners.get(source.sourceKey); }

	async startNew(source: RuntimeAgentSource): Promise<void> {
		const guest = this.guest(source);
		this.cancelPending();
		await guest.startNew();
	}
	async retry(source: RuntimeAgentSource): Promise<void> { await this.guest(source).retry(); }

	cancelSource(source: RuntimeAgentSource): void {
		const key = `${source.sourceKey}:${trustedUrl(source.url).origin}`;
		this.guests.get(key)?.cancelPending();
	}

	/** Pins credentials to configured origin and refuses redirects/card pivots. */
	fetch(source: RuntimeAgentSource, signal?: AbortSignal): typeof fetch {
		const destination = trustedUrl(source.url).origin;
		const epoch = this.signal;
		return async (input, init) => {
			const request = input instanceof Request ? input : undefined;
			const url = new URL(request?.url ?? String(input));
			if (url.origin !== destination || url.username || url.password) {
				throw new Error("Untrusted credential destination.");
			}
			const signals = [epoch, signal, init?.signal, request?.signal].filter(
				(value): value is AbortSignal => !!value
			);
			let lifetime = AbortSignal.any(signals);
			lifetime.throwIfAborted();
			const headers = new Headers(init?.headers ?? request?.headers);
			const explicit = this.explicitHeaders(source);
			for (const [key, value] of explicit) headers.set(key, value);
			let guest: AnonymousSession | undefined;
			if (this.isRegistry(source)) {
				const token = await this.options.accountToken();
				if (!token) throw new Error("This registry requires an account. Run /login to sign in.");
				headers.set("Authorization", `Bearer ${token}`);
				// The account provider establishes trust; decoding here only partitions local cache.
				let owner = token;
				try {
					const claims = JSON.parse(Buffer.from(token.split(".")[1], "base64url").toString());
					if (typeof claims.sub === "string") owner = `${claims.iss}:${claims.sub}`;
				} catch { /* Opaque account credentials get conservative token-scoped caching. */ }
				this.owners.set(source.sourceKey, `account:${hashValue(owner)}`);
			} else if (source.type === "registry") {
				throw new Error("Account credentials are restricted to the selected control-plane registry.");
			} else if (explicit.has("Authorization")) {
				this.owners.set(source.sourceKey, `explicit:${hashValue(explicit.get("Authorization")!)}`);
			} else {
				guest = this.guest(source);
				lifetime = AbortSignal.any([lifetime, guest.signal]);
				headers.set("Authorization", `Bearer ${await guest.token()}`);
				this.owners.set(source.sourceKey, `guest:${guest.owner}`);
			}
			lifetime.throwIfAborted();
			const response = await this.fetcher(input, {
				...init, headers, credentials: "omit", redirect: "error", signal: lifetime
			});
			lifetime.throwIfAborted();
			if (response.status === 401) {
				await response.body?.cancel();
				if (guest) { guest.reject(); await guest.token(); }
				throw new Error("Authentication failed. Run /login to sign in again or check the explicit source credentials.");
			}
			return response;
		};
	}

	/** Login/source switches retire outstanding requests, not stored guests. */
	cancelPending(): void {
		this.lifetime.abort();
		this.lifetime = new AbortController();
		for (const guest of this.guests.values()) guest.cancelPending();
	}

	dispose(): void {
		this.lifetime.abort();
		for (const guest of this.guests.values()) guest.dispose();
	}
}
