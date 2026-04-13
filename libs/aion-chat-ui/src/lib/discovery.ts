export interface DiscoveryManifest {
	api_version?: string;
	name?: string;
	endpoints?: Record<string, string>;
}

export interface DiscoveredAgent {
	id: string;
	path: string;
}

export interface DiscoveryResult {
	rootUrl: string;
	manifestUrl: string;
	agents: DiscoveredAgent[];
}

const MANIFEST_PATH = "/.well-known/manifest.json";
const AGENT_CARD_PATH = "/.well-known/agent-card.json";
const AGENT_PROXY_PREFIX = "/agents/";

function normalizeBase(url: string): URL {
	const parsed = new URL(url);
	if (!parsed.pathname || parsed.pathname === "/") {
		parsed.pathname = "/";
		return parsed;
	}

	if (parsed.pathname.endsWith(MANIFEST_PATH)) {
		parsed.pathname = parsed.pathname.slice(0, -MANIFEST_PATH.length) || "/";
		return parsed;
	}

	if (parsed.pathname.endsWith(AGENT_CARD_PATH)) {
		parsed.pathname = parsed.pathname.slice(0, -AGENT_CARD_PATH.length) || "/";
		return parsed;
	}

	const proxyIndex = parsed.pathname.indexOf(AGENT_PROXY_PREFIX);
	if (proxyIndex >= 0) {
		parsed.pathname = parsed.pathname.slice(0, proxyIndex) || "/";
		return parsed;
	}

	return parsed;
}

function trimTrailingSlash(value: string): string {
	return value.endsWith("/") ? value.slice(0, -1) : value;
}

export function getManifestUrl(url: string): string {
	const base = normalizeBase(url);
	return new URL(MANIFEST_PATH, base).toString();
}

export async function discoverAgents(
	url: string,
	fetchImpl: typeof fetch = fetch
): Promise<DiscoveryResult> {
	const base = normalizeBase(url);
	const rootUrl = trimTrailingSlash(base.toString());
	const manifestUrl = getManifestUrl(url);
	const response = await fetchImpl(manifestUrl);
	if (!response.ok) {
		throw new Error(
			`Failed to fetch manifest from ${manifestUrl}: ${response.status}`
		);
	}

	const manifest = (await response.json()) as DiscoveryManifest;
	const agents = Object.entries(manifest.endpoints ?? {})
		.map(([id, path]) => ({
			id,
			path
		}))
		.sort((left, right) => left.id.localeCompare(right.id));

	return {
		rootUrl,
		manifestUrl,
		agents
	};
}
