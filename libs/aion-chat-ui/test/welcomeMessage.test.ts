import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { AgentCard, Message, SendMessageRequest, Role } from "@a2a-js/sdk";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createChatThread, buildWelcomeRequest, isWelcomeRequest,
	WELCOME_MESSAGE_EXTENSION_URI as uri, WELCOME_REQUEST_SCHEMA as schema
} from "../src/lib/welcomeMessage.js";
import type { ConnectedClient } from "../src/lib/connection.js";
import { buildAuthenticatedFetch, buildMessageParams } from "../src/lib/connection.js";
import { makeTextPart } from "../src/lib/a2aProtocol.js";
import { shouldRenderLiveResponseMessage } from "../src/lib/chatSession.js";
import { loadMostRecentSession, saveCompletedExchange } from "../src/lib/agents/sessionStore.js";

function nativeMessage(fields: Partial<Message>): Message {
	return { messageId: "", role: Role.ROLE_USER, contextId: "", taskId: "", parts: [],
		metadata: undefined, extensions: [], referenceTaskIds: [], ...fields };
}
const greeting = nativeMessage({ messageId: "greeting", role: Role.ROLE_AGENT,
	contextId: "context", parts: [makeTextPart("Hello!")], extensions: [uri] });
function connected(sendMessage = vi.fn().mockResolvedValue(greeting), enabled = true): ConnectedClient {
	return {
		agentCard: AgentCard.fromJSON({ capabilities: { streaming: false,
			extensions: enabled ? [{ uri, required: false }] : [] } }),
		client: { sendMessage } as unknown as ConnectedClient["client"],
		endpoints: { baseUrl: "https://agent", rpcUrl: "https://agent/rpc", cardUrl: "https://agent/card", cardPath: "/card" }
	};
}
const directories: string[] = [];
afterEach(() => { vi.unstubAllGlobals(); for (const dir of directories.splice(0)) rmSync(dir, { recursive: true, force: true }); });

describe("terminal new-thread welcome", () => {
	it("assigns context first and selects unary with both activation layers", async () => {
		let context: string | undefined;
		const send = vi.fn().mockImplementation((request) => {
			expect(context).toBe("new-context");
			expect(request.message.contextId).toBe(context);
			return Promise.resolve(greeting);
		});
		const onWelcome = vi.fn();
		createChatThread({ connected: connected(send), createId: () => "new-context",
			onCreated: (id) => { context = id; }, onWelcome, onError: vi.fn() });
		expect(context).toBe("new-context");
		// An immediate ordinary message uses the committed ID and no welcome activation.
		const ordinary = buildMessageParams([makeTextPart("Question")], context, undefined);
		expect(ordinary.message?.contextId).toBe("new-context");
		expect(ordinary.message?.extensions).toEqual([]);
		expect(send).toHaveBeenCalledTimes(1);
		const [request, options] = send.mock.calls[0]!;
		expect(options.serviceParameters).toEqual({ "A2A-Extensions": uri });
		expect(SendMessageRequest.toJSON(request)).toMatchObject({ message: {
			extensions: [uri], parts: [{ data: { type: "welcome-request" }, metadata: { [uri]: { schema } } }]
		} });
		await vi.waitFor(() => expect(onWelcome).toHaveBeenCalledOnce());
	});

	it("creates distinct attempts only for explicit actions and never retries a failure", async () => {
		const send = vi.fn().mockRejectedValue(new Error("lost response"));
		const onError = vi.fn();
		const connection = connected(send);
		const options = { connected: connection, onCreated: vi.fn(), onWelcome: vi.fn(), onError };
		const first = createChatThread(options);
		await vi.waitFor(() => expect(onError).toHaveBeenCalledOnce());
		expect(send).toHaveBeenCalledTimes(1);
		// Reading a reconnected card is not thread creation.
		expect(connection.agentCard.capabilities?.extensions[0]?.uri).toBe(uri);
		expect(send).toHaveBeenCalledTimes(1);
		const second = createChatThread(options);
		expect(second).not.toBe(first);
		await vi.waitFor(() => expect(onError).toHaveBeenCalledTimes(2));
		expect(send).toHaveBeenCalledTimes(2);
	});

	it("skips unsupported cards and ignores canceled completion", async () => {
		const send = vi.fn().mockResolvedValue(greeting);
		const onWelcome = vi.fn();
		createChatThread({ connected: connected(send, false), onCreated: vi.fn(), onWelcome, onError: vi.fn() });
		expect(send).not.toHaveBeenCalled();
		const controller = new AbortController();
		createChatThread({ connected: connected(send), signal: controller.signal, onCreated: vi.fn(), onWelcome, onError: vi.fn() });
		controller.abort();
		await Promise.resolve();
		expect(onWelcome).not.toHaveBeenCalled();
		expect(send).toHaveBeenCalledTimes(1);
	});

	it("preserves request activation when custom distribution headers are present", async () => {
		const fetcher = vi.fn().mockResolvedValue(new Response("{}"));
		vi.stubGlobal("fetch", fetcher);
		await buildAuthenticatedFetch({ headers: { "a2a-extensions": "distribution-extension" } })("https://agent", {
			headers: { "A2A-Extensions": uri }
		});
		expect(new Headers(fetcher.mock.calls[0]![1].headers).get("A2A-Extensions")).toBe(`${uri},distribution-extension`);
	});

	it("keeps both replies and markers when welcome completes after ordinary chat", () => {
		const dir = mkdtempSync(path.join(os.tmpdir(), "welcome-session-")); directories.push(dir);
		const user = nativeMessage({ messageId: "user", role: Role.ROLE_USER, parts: [makeTextPart("Question")] });
		const answer = nativeMessage({ ...greeting, messageId: "answer", extensions: [] });
		const snapshot = { environment: "development" as const, agentKey: "agent", contextId: "context" };
		saveCompletedExchange({ ...snapshot, messages: [user, answer] }, dir);
		const trigger = buildWelcomeRequest("context").message!;
		saveCompletedExchange({ ...snapshot, messages: [trigger, greeting] }, dir);
		const restored = loadMostRecentSession("development", "agent", dir)!;
		expect(restored.messages.map((message) => message.messageId)).toEqual(["user", "answer", trigger.messageId, "greeting"]);
		expect(isWelcomeRequest(restored.messages[2]!)).toBe(true);
		expect(restored.messages[3]!.extensions).toEqual([uri]);
		expect(restored.messages.filter(shouldRenderLiveResponseMessage)).toEqual([answer, greeting]);
		expect(isWelcomeRequest({ ...trigger, parts: [...trigger.parts, makeTextPart("Actual text")] })).toBe(false);
	});
});
