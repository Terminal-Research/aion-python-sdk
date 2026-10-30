export interface ChatOperation {
	kind: "reply" | "workspace";
	signal: AbortSignal;
	finish(): void;
	cancel(): void;
}

/** Synchronous exclusion before the first await; cancelled work cannot unlock newer work. */
export class ChatOperationGate {
	private active?: { operation: ChatOperation; controller: AbortController };
	get kind(): ChatOperation["kind"] | undefined { return this.active?.operation.kind; }
	get busy(): boolean { return !!this.active; }
	get reason(): string | undefined {
		if (this.active?.operation.kind === "reply") return "Please wait for the current reply to finish before switching workspaces.";
		if (this.active) return "A workspace change is in progress. Please wait for it to finish.";
		return undefined;
	}
	begin(kind: ChatOperation["kind"]): ChatOperation | undefined {
		if (this.active) return undefined;
		const controller = new AbortController();
		const operation: ChatOperation = {
			kind, signal: controller.signal,
			cancel: () => { controller.abort(); operation.finish(); },
			finish: () => { if (this.active?.operation === operation) this.active = undefined; }
		};
		this.active = { operation, controller };
		return operation;
	}
	cancel(): void {
		this.active?.controller.abort();
		this.active = undefined;
	}
}
