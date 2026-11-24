import { randomUUID } from "node:crypto";
import { Role, TaskState, type Artifact, type Message, type Part, type Task, type TaskStatus } from "@a2a-js/sdk";
import {
	ASSISTANT_ANSWER_ARTIFACT_ID,
	STREAM_DELTA_ARTIFACT_ID
} from "./a2aMetadata.js";
import {
	isAgentMessage, isMessage, isTask, isTaskArtifactUpdateEvent,
	isTaskContinuationState, isTaskStatusUpdateEvent, isTerminalTaskState,
	makeTextPart, type StreamEvent
} from "./a2aProtocol.js";
import { getTaskMessages } from "./messageDisplay.js";

const EVENT_EXTENSION = "https://docs.aion.to/a2a/extensions/aion/event/1.0.0";

/** Read answer text independently of file and structured-payload rendering. */
function textOf(parts: Part[], separator: string): string {
	return parts.flatMap((part) => part.content?.$case === "text"
		&& part.content.value && !part.metadata?.[EVENT_EXTENSION] ? [part.content.value] : []).join(separator);
}

function isConversationMessage(message: Message): boolean {
	const kind = message.metadata?.["aion:messageType"];
	return kind === undefined || kind === "message";
}

/**
 * One turn: latest Assistant Answer, current-turn messages, then Stream Delta.
 * Artifact content is self-contained; source IDs only associate duplicates.
 * A present empty or file-only answer stops message/stream fallback.
 * Status prompts and failure explanations remain visible independently.
 */
export class ResponseAnswer {
	readonly entryId: string;
	private taskId = "";
	private contextId = "";
	private assistant?: Artifact;
	private stream?: Artifact;
	private messages: Message[] = [];
	private notice?: Message;
	private baseline = new Set<string>();
	private baselineStream?: string;
	private opened = false;

	constructor(private readonly requestMessageId?: string) {
		this.entryId = `answer:${requestMessageId ?? randomUUID()}`;
	}

	/** Updates append only when requested; snapshots replace complete artifacts. */
	observe(event: StreamEvent): void {
		const taskId = isTask(event) ? event.id : event.taskId;
		if (taskId && this.taskId && taskId !== this.taskId) {
			this.assistant = undefined;
			this.stream = undefined;
			this.messages = [];
			this.notice = undefined;
			this.baseline.clear();
			this.baselineStream = undefined;
			this.opened = false;
		}
		this.taskId = taskId || this.taskId;
		this.contextId = event.contextId || this.contextId;
		if (isTaskArtifactUpdateEvent(event)) {
			const artifact = event.artifact;
			if (!artifact) return;
			if (artifact.artifactId === ASSISTANT_ANSWER_ARTIFACT_ID) {
				this.assistant = this.merge(this.assistant, artifact, event.append);
			} else if (artifact.artifactId === STREAM_DELTA_ARTIFACT_ID) {
				// An empty closing marker is not a new stream section.
				if (event.append || textOf(artifact.parts, "").trim()) {
					this.stream = this.merge(this.stream, artifact, event.append);
				}
			}
		} else if (isTask(event)) {
			this.observeTask(event);
		} else if (isMessage(event)) {
			if (isAgentMessage(event) && isConversationMessage(event)) this.messages = [event];
		} else if (isTaskStatusUpdateEvent(event)
			&& (isTerminalTaskState(event.status?.state) || isTaskContinuationState(event.status?.state))) {
			this.observeNotice(event.status);
			const message = event.status?.message;
			if (message && isAgentMessage(message) && isConversationMessage(message)) {
				this.messages = [message];
			}
		}
	}

	/** Produce a self-contained message for display and saved transcript restore. */
	message(): Message | undefined {
		const message = this.selectMessage();
		if (!message || this.notice || this.assistant) return message;
		// File display is independent of which source supplies canonical text.
		const candidates = this.messages.flatMap((value) => value.parts);
		const seen = new Set(message.parts.map((part) => JSON.stringify(part)));
		const files = candidates.filter((part) => {
			if (part.content?.$case !== "url" && part.content?.$case !== "raw") return false;
			const key = JSON.stringify(part);
			if (seen.has(key)) return false;
			seen.add(key);
			return true;
		});
		return { ...message, parts: [...message.parts, ...files] };
	}

	private selectMessage(): Message | undefined {
		if (this.notice) return { ...this.notice, messageId: this.entryId, taskId: this.taskId };
		if (this.assistant) {
			return this.fromArtifact(this.assistant, "\n");
		}
		const text = this.messages.map((message) => textOf(message.parts, "\n"))
			.filter((value) => value.trim()).join("\n\n");
		if (text) {
			const last = this.messages.at(-1)!;
			return { ...last, messageId: this.entryId, taskId: this.taskId,
				parts: [makeTextPart(text), ...this.messages.flatMap((message) =>
					message.parts.filter((part) => part.content?.$case !== "text"))] };
		}
		if (this.stream && textOf(this.stream.parts, "").trim()) {
			return this.fromArtifact(this.stream, "");
		}
		// Non-text messages remain inspectable without becoming answer text.
		const last = this.messages.at(-1);
		if (last?.parts.length) return { ...last, messageId: this.entryId, taskId: this.taskId };
		return undefined;
	}

	/** Source IDs associate rendered duplicates; they never gate artifact text. */
	replacedEntryIds(): Set<string> {
		const source = this.assistant?.metadata?.["aion:sourceMessageId"];
		const ids = this.notice ? [this.notice.messageId] : this.assistant
			? (typeof source === "string" ? [source] : [])
			: this.messages.map((message) => message.messageId);
		return new Set([this.entryId, ...ids.map((id) => `message:${this.taskId || "no-task"}:${id}`)]);
	}

	private observeTask(task: Task): void {
		this.observeNotice(task.status);
		const terminal = isTerminalTaskState(task.status?.state) || isTaskContinuationState(task.status?.state);
		const opening = !this.opened && !terminal;
		const history = getTaskMessages(task).filter(isConversationMessage);
		const requestIndex = this.requestMessageId
			? history.findIndex((message) => message.messageId === this.requestMessageId) : -1;
		const lastUserIndex = history.map((message) => message.role).lastIndexOf(Role.ROLE_USER);
		const turnIndex = requestIndex >= 0 ? requestIndex : lastUserIndex;
		if (opening && requestIndex < 0) {
			for (const message of history) this.baseline.add(message.messageId);
		}
		this.opened = true;
		if (terminal) {
			const unanchored = this.requestMessageId && requestIndex < 0 && lastUserIndex >= 0;
			const observed = new Set(this.messages.map((message) => message.messageId));
			this.messages = history.slice(turnIndex + 1).filter((message) =>
				isAgentMessage(message) && !this.baseline.has(message.messageId)
				&& (!unanchored || observed.has(message.messageId)));
		}
		for (const artifact of task.artifacts ?? []) {
			if (artifact.artifactId === ASSISTANT_ANSWER_ARTIFACT_ID) this.assistant = artifact;
			else if (artifact.artifactId === STREAM_DELTA_ARTIFACT_ID) {
				const snapshot = JSON.stringify(artifact);
				if (opening) this.baselineStream = snapshot;
				else if (terminal && snapshot !== this.baselineStream) this.stream = artifact;
			}
		}
	}

	/** Pause prompts and failure explanations remain visible as task status. */
	private observeNotice(status: TaskStatus | undefined): void {
		const message = status?.message;
		if (status?.state !== TaskState.TASK_STATE_COMPLETED
			&& (isTerminalTaskState(status?.state) || isTaskContinuationState(status?.state))
			&& message && isAgentMessage(message) && isConversationMessage(message)) {
			this.notice = message;
		}
	}

	private merge(previous: Artifact | undefined, next: Artifact, append?: boolean): Artifact {
		return append && previous ? { ...next,
			parts: [...previous.parts, ...next.parts],
			metadata: { ...previous.metadata, ...next.metadata },
			extensions: [...new Set([...previous.extensions, ...next.extensions])]
		} : next;
	}

	private fromArtifact(artifact: Artifact, separator: string): Message {
		const text = textOf(artifact.parts, separator);
		return {
			messageId: this.entryId, taskId: this.taskId, contextId: this.contextId,
			role: Role.ROLE_AGENT, referenceTaskIds: [], metadata: artifact.metadata, extensions: artifact.extensions,
			parts: [...(text.trim() ? [makeTextPart(text)] : []),
				...artifact.parts.filter((part) => part.content?.$case !== "text")]
		};
	}
}

/** Select a unary response without requiring a live accumulator. */
export function responseMessages(response: Message | Task, requestMessageId?: string): Message[] {
	const answer = new ResponseAnswer(requestMessageId);
	answer.observe(response);
	const message = answer.message();
	return message ? [message] : [];
}
