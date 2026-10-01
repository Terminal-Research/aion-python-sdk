/** Overlay configured headers without losing per-operation extension intent. */
export function mergeRequestHeaders(
	requestHeaders: HeadersInit | undefined,
	configuredHeaders: Iterable<readonly [string, string]>
): Headers {
	const headers = new Headers(requestHeaders);
	for (const [key, value] of configuredHeaders) {
		if (key.toLowerCase() === "a2a-extensions") {
			const extensions = [headers.get(key) ?? "", value]
				.flatMap((entry) => entry.split(",")).map((entry) => entry.trim())
				.filter(Boolean);
			headers.set(key, [...new Set(extensions)].join(","));
		} else {
			headers.set(key, value);
		}
	}
	return headers;
}
