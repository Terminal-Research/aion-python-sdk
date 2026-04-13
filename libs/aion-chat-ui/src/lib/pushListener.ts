import http from "node:http";

export interface PushNotificationEvent {
	kind: "validation" | "notification";
	payload: string | unknown;
}

export interface PushNotificationServer {
	callbackUrl: string;
	close(): Promise<void>;
}

export async function startPushNotificationServer(
	receiverUrl: string,
	onEvent: (event: PushNotificationEvent) => void
): Promise<PushNotificationServer> {
	const parsed = new URL(receiverUrl);
	if (parsed.protocol !== "http:") {
		throw new Error(
			`Push notification receiver must use http://, received '${receiverUrl}'.`
		);
	}
	const hostname = parsed.hostname || "127.0.0.1";
	const port = Number(parsed.port || "80");

	const server = http.createServer(async (request, response) => {
		const requestUrl = new URL(request.url ?? "/", parsed.origin);
		if (requestUrl.pathname !== "/notify") {
			response.writeHead(404).end();
			return;
		}

		if (request.method === "GET") {
			const token = requestUrl.searchParams.get("validationToken");
			onEvent({ kind: "validation", payload: token ?? "" });
			if (!token) {
				response.writeHead(400).end();
				return;
			}

			response.writeHead(200, { "Content-Type": "text/plain" }).end(token);
			return;
		}

		if (request.method === "POST") {
			const chunks: Uint8Array[] = [];
			for await (const chunk of request) {
				chunks.push(chunk);
			}

			const body = Buffer.concat(chunks).toString("utf8");
			let payload: unknown = body;
			try {
				payload = JSON.parse(body);
			} catch {
				// Keep raw payload if it is not JSON.
			}

			onEvent({ kind: "notification", payload });
			response.writeHead(200).end();
			return;
		}

		response.writeHead(405).end();
	});

	await new Promise<void>((resolve, reject) => {
		server.once("error", reject);
		server.listen(port, hostname, () => {
			server.off("error", reject);
			resolve();
		});
	});

	const address = server.address();
	const boundPort =
		address && typeof address === "object" ? address.port : port;

	return {
		callbackUrl: `http://${hostname}:${boundPort}/notify`,
		close: async () => {
			await new Promise<void>((resolve, reject) => {
				server.close((error) => {
					if (error) {
						reject(error);
						return;
					}
					resolve();
				});
			});
		}
	};
}
