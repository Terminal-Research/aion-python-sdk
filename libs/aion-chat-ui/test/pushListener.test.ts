import { afterEach, describe, expect, it } from "vitest";

import {
	type PushNotificationServer,
	startPushNotificationServer
} from "../src/lib/pushListener.js";

describe("startPushNotificationServer", () => {
	let server: PushNotificationServer | undefined;

	afterEach(async () => {
		if (server) {
			await server.close();
			server = undefined;
		}
	});

	it("handles validation and notification callbacks", async () => {
		const events: Array<{ kind: string; payload: unknown }> = [];
		server = await startPushNotificationServer(
			"http://127.0.0.1:0",
			(event) => events.push(event)
		);

		const validationResponse = await fetch(
			`${server.callbackUrl}?validationToken=hello`
		);
		expect(validationResponse.status).toBe(200);
		expect(await validationResponse.text()).toBe("hello");

		const payload = { ok: true };
		const notificationResponse = await fetch(server.callbackUrl, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(payload)
		});
		expect(notificationResponse.status).toBe(200);
		expect(events).toEqual([
			{ kind: "validation", payload: "hello" },
			{ kind: "notification", payload }
		]);
	});

	it("rejects non-http receiver URLs", async () => {
		await expect(
			startPushNotificationServer("https://127.0.0.1:5000", () => {})
		).rejects.toThrow("must use http://");
	});
});
