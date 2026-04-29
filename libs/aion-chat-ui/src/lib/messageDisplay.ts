import type { Artifact, FilePart, Message, Task } from "@a2a-js/sdk";

function formatFilePart(part: FilePart): string {
	const file = part.file;
	const metadata: Record<string, unknown> = {};

	if (file.name) {
		metadata.name = file.name;
	}
	if (file.mimeType) {
		metadata.mimeType = file.mimeType;
	}
	if ("uri" in file) {
		metadata.uri = file.uri;
	}
	if ("bytes" in file) {
		metadata.bytesBase64Length = file.bytes.length;
	}
	if (part.metadata) {
		metadata.metadata = part.metadata;
	}

	return `File part returned:\n${JSON.stringify(metadata, null, 2)}`;
}

export function formatMessageParts(parts: Message["parts"] | Artifact["parts"]): string {
	return parts
		.map((part) => {
			if (part.kind === "text") {
				return part.text;
			}
			if (part.kind === "file") {
				return formatFilePart(part);
			}
			if (part.kind === "data") {
				return JSON.stringify(part.data, null, 2);
			}
			return "";
		})
		.filter(Boolean)
		.join("\n");
}

export function getTaskMessages(task: Task): Message[] {
	const statusMessage = task.status.message as Message | undefined;
	if (task.history && task.history.length > 0) {
		const history = task.history as Message[];
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
