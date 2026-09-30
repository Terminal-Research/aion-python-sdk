import { randomUUID } from "node:crypto";
import { Role, type Message, type SendMessageRequest } from "@a2a-js/sdk";
import type { ConnectedClient } from "./connection.js";
import { buildMessageParams } from "./connection.js";

export const WELCOME_MESSAGE_EXTENSION_URI =
	"https://docs.aion.to/a2a/extensions/aion/welcome-message/1.0.0";
export const WELCOME_REQUEST_SCHEMA =
	`${WELCOME_MESSAGE_EXTENSION_URI}#WelcomeRequestPayload`;

/** Build request-scoped welcome intent using the normal protocol metadata. */
export function buildWelcomeRequest(contextId: string): SendMessageRequest {
	const request = buildMessageParams([{
		content: { $case: "data", value: { type: "welcome-request" } },
		metadata: {
			[WELCOME_MESSAGE_EXTENSION_URI]: { schema: WELCOME_REQUEST_SCHEMA }
		},
		filename: "",
		mediaType: "application/json"
	}], contextId, undefined);
	request.message!.extensions = [WELCOME_MESSAGE_EXTENSION_URI];
	return request;
}

/** Real user text stays visible even if welcome intent was also supplied. */
export function isWelcomeRequest(message: Message): boolean {
	return message.role === Role.ROLE_USER
		&& !message.parts.some((part) =>
			part.content?.$case === "text" && part.content.value.trim())
		&& message.parts.some((part) =>
			part.content?.$case === "data"
			&& part.content.value?.type === "welcome-request"
			&& part.metadata?.[WELCOME_MESSAGE_EXTENSION_URI]?.schema === WELCOME_REQUEST_SCHEMA);
}

export interface NewChatThreadOptions {
	connected?: ConnectedClient;
	/** Commit the context before either welcome or user dispatch can happen. */
	onCreated: (contextId: string) => void;
	onWelcome: (request: SendMessageRequest, response: Awaited<ReturnType<ConnectedClient["client"]["sendMessage"]>>) => void;
	onError: (error: unknown, contextId: string) => void;
	signal?: AbortSignal;
	createId?: () => string;
}

/** The explicit creation action owns one send; reconnect never invokes this. */
export function createChatThread(options: NewChatThreadOptions): string {
	const contextId = (options.createId ?? randomUUID)();
	options.onCreated(contextId);
	const connected = options.connected;
	if (options.signal?.aborted || !connected?.agentCard.capabilities?.extensions?.some(
		(extension) => extension.uri === WELCOME_MESSAGE_EXTENSION_URI
	)) return contextId;
	const request = buildWelcomeRequest(contextId);
	void connected.client.sendMessage(request, {
		signal: options.signal,
		serviceParameters: { "A2A-Extensions": WELCOME_MESSAGE_EXTENSION_URI }
	}).then((response) => {
		if (!options.signal?.aborted) options.onWelcome(request, response);
	})
		.catch((error) => {
			if (!options.signal?.aborted) options.onError(error, contextId);
		});
	return contextId;
}
