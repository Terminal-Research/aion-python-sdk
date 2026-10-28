import { Role, type Message } from "@a2a-js/sdk";
import type { TranscriptEntry } from "../components/MessageBubble.js";
import { shouldRenderLiveResponseMessage } from "./chatSession.js";
import { formatMessageParts } from "./messageDisplay.js";
import { WELCOME_MESSAGE_EXTENSION_URI } from "./welcomeMessage.js";

/** Render saved messages without replaying task events, welcomes or writes. */
export function restoredTranscriptEntries(messages: Message[]): TranscriptEntry[] {
	return messages.flatMap((message) => {
		if (message.role === Role.ROLE_USER && message.extensions?.includes(WELCOME_MESSAGE_EXTENSION_URI)) return [];
		if (message.role === Role.ROLE_AGENT && !shouldRenderLiveResponseMessage(message)) return [];
		const body = formatMessageParts(message.parts);
		return body ? [{ id: message.messageId, role: message.role === Role.ROLE_USER ? "user" : "agent", body, isFinalized: true }] : [];
	});
}
