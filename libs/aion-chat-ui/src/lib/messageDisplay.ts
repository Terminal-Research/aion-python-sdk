import type { Artifact, Message, Part, Task } from "@a2a-js/sdk";

function formatRawPart(part: Part): string {
	const metadata: Record<string, unknown> = {};
	if (part.filename) {
		metadata.name = part.filename;
	}
	if (part.mediaType) {
		metadata.mimeType = part.mediaType;
	}
	if (part.content?.$case === "raw") {
		metadata.bytesLength = part.content.value.length;
	}
	if (part.content?.$case === "url") {
		metadata.uri = part.content.value;
	}
	if (part.metadata) {
		metadata.metadata = part.metadata;
	}
	return `File part returned:\n${JSON.stringify(metadata, null, 2)}`;
}

export function formatMessageParts(parts: Message["parts"] | Artifact["parts"]): string {
	return parts
		.map((part) => {
			switch (part.content?.$case) {
				case "text":
					return part.content.value;
				case "raw":
				case "url":
					return formatRawPart(part);
				case "data":
					return JSON.stringify(part.content.value, null, 2);
				default:
					return "";
			}
		})
		.filter(Boolean)
		.join("\n");
}

export function getTaskMessages(task: Task): Message[] {
	const statusMessage = task.status?.message;
	if (task.history && task.history.length > 0) {
		const history = task.history;
		if (
			statusMessage &&
			!history.some((message) => message.messageId === statusMessage.messageId)
		) {
			return [...history, statusMessage];
		}
		return history;
	}
	return statusMessage ? [statusMessage] : [];
}
