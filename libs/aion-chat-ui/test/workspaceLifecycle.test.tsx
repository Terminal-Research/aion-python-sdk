import React, { act } from "react";
import { render } from "ink-testing-library";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentCard, Message } from "@a2a-js/sdk";
import { ChatApp } from "../src/app.js";
import { registryAccountScope, registryAgentKey } from "../src/lib/workspaces.js";
import { createDefaultRegistryAgentSource, hashValue, type AgentContextSessionFile } from "../src/lib/agents/model.js";
import { WELCOME_MESSAGE_EXTENSION_URI } from "../src/lib/welcomeMessage.js";

const state = vi.hoisted(() => ({
	settings: {} as import("../src/lib/chatSettings.js").ChatSettings,
	sessions: new Map<string, AgentContextSessionFile>(),
	saves: [] as any[], loads: [] as string[], token: "", errors: [] as string[], accountId: "user"
}));
vi.mock("../src/lib/workosAuth.js", () => ({
	getStoredAccessToken: async () => state.token,
	loginWithWorkOS: vi.fn(async () => ({ accessToken: state.token }))
}));
vi.mock("../src/lib/chatSettings.js", async (original) => ({
	...await original<typeof import("../src/lib/chatSettings.js")>(),
	loadChatSettings: () => ({ settings: state.settings }),
	saveChatSettings: (settings: typeof state.settings) => { state.settings = settings; }
}));
vi.mock("../src/lib/agents/sessionStore.js", () => ({
	resolveSessionFilePath: () => "/test-only/session.json",
	saveCompletedExchange: (snapshot: any) => {
		state.saves.push(snapshot);
		const old = state.sessions.get(snapshot.agentKey);
		state.sessions.set(snapshot.agentKey, { ...old, ...snapshot, localTurnCount: (old?.localTurnCount ?? 0) + 1 });
	},
	loadContextSession: (_env: string, key: string, contextId: string) => {
		state.loads.push(key);
		const session = state.sessions.get(key);
		return session?.contextId === contextId ? { session } : { warning: "This conversation is still selected, but its saved history could not be loaded." };
	},
	copyContextSession: vi.fn()
}));
vi.mock("../src/lib/credentialStore.js", () => ({ defaultCredentialStore: {
	getAnonymousSession: async () => undefined, setAnonymousSession: async () => undefined
} }));
vi.mock("../src/lib/sessionLogger.js", async (original) => ({ ...await original<typeof import("../src/lib/sessionLogger.js")>(), createChatSessionLogger: () => ({ logger: {
	chatSessionId: "test", logFilePath: "/test-only/log.jsonl",
	debug: vi.fn(), info: vi.fn(), warn: (_event: string, data: any) => { if (data?.error) state.errors.push(String(data.error)); }, error: vi.fn(), flush: vi.fn()
} }) }));
vi.mock("../src/lib/browser.js", () => ({ openUrlInDefaultBrowser: async () => false }));

const source = createDefaultRegistryAgentSource("production");
const accountScope = registryAccountScope("production", "user");
const keyFor = (workspace: string) => registryAgentKey(source.sourceKey, "shared", accountScope, workspace);
const options = { headers: {}, pushNotifications: false, pushReceiver: "http://localhost:5000" };
let app: ReturnType<typeof render> | undefined;
let catalogs: string[], membershipCalls: number, cardCalls: number;
let holdMembership: (() => Promise<void>) | undefined;
let holdCard: ((count: number) => Promise<void>) | undefined;
let failMembership: boolean, welcome: boolean;
let rpc: Array<{ request: Request; body: any }>;
let rpcReply: ((request: Request, body: any) => Promise<Response>) | undefined;

function deferred() {
	let resolve!: () => void;
	const promise = new Promise<void>((done) => { resolve = done; });
	return { promise, resolve };
}
function reply(body: any, text: string): Response {
	const result = { jsonrpc: "2.0", id: body.id, result: { message: {
		messageId: text, role: "ROLE_AGENT", contextId: body.params.message.contextId, parts: [{ text }]
	} } };
	return body.method === "SendStreamingMessage"
		? new Response('data: ' + JSON.stringify(result) + '\n\n', { headers: { "Content-Type": "text/event-stream" } })
		: Response.json(result);
}
async function enter(text: string) {
	await act(async () => { app!.stdin.write(text); });
	await vi.waitFor(() => expect(app!.lastFrame()).toContain('› ' + text));
	await act(async () => { app!.stdin.write("\r"); });
}
async function ready(workspace = "home") {
	await vi.waitFor(() => expect(state.loads, app?.frames.join("\n") + JSON.stringify({ catalogs, membershipCalls, cardCalls, errors: state.errors })).toContain(keyFor(workspace)));
}
function savedChoice() {
	return state.settings.environments.production.registryAccounts?.[accountScope]?.workspaceId;
}
beforeEach(() => {
	state.accountId = "user"; state.sessions.clear(); state.saves = []; state.loads = []; state.errors = [];
	state.token = 'header.' + Buffer.from(JSON.stringify({ iss: "issuer", sub: "user" })).toString("base64url") + '.test';
	catalogs = []; membershipCalls = 0; cardCalls = 0; rpc = [];
	holdMembership = undefined; holdCard = undefined; rpcReply = undefined; failMembership = false; welcome = false;
	const base = { requestMode: "send-message" as const, responseMode: "message-output" as const, agentSources: {}, agents: {} };
	state.settings = { selectedEnvironment: "production", environments: { production: { ...base }, staging: { ...base }, development: { ...base } } };
	const env = state.settings.environments.production;
	env.registryAccounts = { [accountScope]: { workspaceId: "home", selectedAgentKeys: {} } };
	for (const id of ["home", "team", "other"]) {
		const key = keyFor(id);
		env.registryAccounts[accountScope].selectedAgentKeys![id] = key;
		env.agents[key] = { agentKey: key, agentId: "shared", sourceKey: source.sourceKey, agentCardUrl: "https://api.aion.to/agent/.well-known/agent-card.json", lastSeenAt: "", activeContextId: id + "-context", credentialScope: "account:" + hashValue("issuer:user") };
		state.sessions.set(key, { schemaVersion: 1, environment: "production", agentKey: key, contextId: id + "-context", createdAt: "", lastUpdatedAt: "", localTurnCount: 1, lastTaskId: "completed",
			messages: [Message.fromJSON({ messageId: id + "-question", role: "ROLE_USER", contextId: id + "-context", parts: [{ text: id + " earlier question" }] }),
				Message.fromJSON({ messageId: id + "-answer", role: "ROLE_AGENT", contextId: id + "-context", parts: [{ text: id + " earlier answer" }] })]
		});
	}
	vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
		const request = new Request(input, init);
		const body = request.method === "POST" && request.headers.has("Content-Type") ? await request.json() : undefined;
		if (request.url.endsWith("/api/graphql")) {
			if (body.query.includes("LoginBootstrap")) return Response.json({ data: { login: {} } });
			if (body.query.includes("CurrentUser")) {
				membershipCalls++;
				await holdMembership?.();
				if (failMembership) return Response.json({ errors: [{ message: "Workspace lookup unavailable" }] });
				return Response.json({ data: { user: { id: state.accountId, agentIdentity: { id: "personal", organizationId: "home" }, homeOrganization: { id: "home", name: "Home", organizationType: "Personal" },
					memberships: ["team", "other"].map((id) => ({ organization: { id, name: id === "team" ? "Terminal Research" : "Other", organizationType: "Team" } })) } } });
			}
			catalogs.push(body.variables.organizationId);
			return Response.json({ data: { agentIdentityDetails: [{ identity: { id: "shared", atName: "@shared", name: "Shared", a2aUrl: "https://api.aion.to/agent" } }] } });
		}
		if (request.url === "https://api.aion.to/agent/.well-known/agent-card.json") {
			cardCalls++;
			await holdCard?.(cardCalls);
			return Response.json(AgentCard.toJSON(AgentCard.fromJSON({
				name: "Shared Agent", description: "Fixture", version: "1",
				supportedInterfaces: [{ url: "https://api.aion.to/agent/", protocolBinding: "JSONRPC", protocolVersion: "1.0" }],
				capabilities: { streaming: true, extensions: welcome ? [{ uri: WELCOME_MESSAGE_EXTENSION_URI }] : [] },
				defaultInputModes: ["text/plain"], defaultOutputModes: ["text/plain"], skills: []
			})));
		}
		if (request.url === "https://api.aion.to/agent/") {
			rpc.push({ request, body });
			return rpcReply ? rpcReply(request, body) : reply(body, "Current reply");
		}
		return new Response(null, { status: 503 });
	}));
});
afterEach(() => { app?.unmount(); app = undefined; vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("workspace UI lifecycle", () => {
	it("restores A to B to A and after restart without new welcomes, turns or active task IDs", async () => {
		welcome = true;
		app = render(<ChatApp options={options} />);
		await ready();
		expect(app.lastFrame()).toContain("Workspace: Home");
		expect(app.frames.join("\n")).toContain("home earlier question");
		await enter('/workspace "Terminal Research"');
		await ready("team");
		expect(savedChoice()).toBe("team");
		expect(app.frames.join("\n")).toContain("team earlier answer");
		await enter("/workspace home");
		await vi.waitFor(() => expect(state.loads.filter((key) => key === keyFor("home"))).toHaveLength(2));
		expect(state.saves).toHaveLength(0);
		expect(rpc).toHaveLength(0);
		app.unmount(); app = render(<ChatApp options={options} />);
		await vi.waitFor(() => expect(state.loads.filter((key) => key === keyFor("home"))).toHaveLength(3));
		await enter("Follow up");
		await vi.waitFor(() => expect(rpc).toHaveLength(1));
		expect(rpc[0].body.params.message.contextId).toBe("home-context");
		expect(rpc[0].body.params.message.taskId ?? "").toBe("");
	});
	it.each(["send-message", "streaming-message"] as const)("refuses switches until the %s reply and its save finish", async (mode) => {
		state.settings.environments.production.requestMode = mode;
		const pending = deferred();
		rpcReply = async (_request, body) => { await pending.promise; return reply(body, "Finished reply"); };
		app = render(<ChatApp options={options} />); await ready();
		await enter("A question");
		await vi.waitFor(() => expect(rpc).toHaveLength(1));
		const before = membershipCalls;
		await enter("/workspace team");
		expect(membershipCalls).toBe(before);
		expect(savedChoice()).toBe("home");
		expect(rpc[0].request.signal.aborted).toBe(false);
		expect(app.frames.join("\n")).toContain("reply to finish");
		await act(async () => { pending.resolve(); });
		await vi.waitFor(() => expect(state.saves).toHaveLength(1));
		await enter("/workspace team"); await ready("team");
		expect(state.saves[0].agentKey).toBe(keyFor("home"));
	});
	it("serializes membership validation and connection restoration, rejecting extra choices and prompts", async () => {
		app = render(<ChatApp options={options} />); await ready();
		const membership = deferred(), connection = deferred();
		holdMembership = () => membership.promise;
		holdCard = async (count) => { if (count === 4) await connection.promise; };
		await enter("/workspace team");
		await vi.waitFor(() => expect(membershipCalls).toBe(2));
		await enter("/workspace other");
		await enter("Do not send");
		// A rejected prompt stays in the composer.
		await act(async () => app!.stdin.write("\u001b"));
		expect(membershipCalls).toBe(2); expect(rpc).toHaveLength(0);
		membership.resolve();
		await vi.waitFor(() => expect(cardCalls).toBe(4));
		await enter("/workspace other");
		expect(membershipCalls).toBe(2); expect(state.loads).not.toContain(keyFor("team"));
		connection.resolve(); await ready("team");
		holdMembership = undefined;
		await enter("/workspace other"); await ready("other");
		expect(catalogs).toEqual(["home", "team", "other"]);
	});
	it("keeps the existing conversation on failed validation and permits a later retry", async () => {
		app = render(<ChatApp options={options} />); await ready();
		failMembership = true;
		await enter("/workspace team");
		await vi.waitFor(() => expect(app!.frames.join("\n")).toContain("Workspace membership lookup failed"));
		expect(savedChoice()).toBe("home");
		expect(state.loads).toEqual([keyFor("home")]);
		failMembership = false;
		await enter("/workspace team"); await ready("team");
	});
	it("rejects a late welcome after switching without writing it into either scope", async () => {
		welcome = true;
		state.settings.environments.production.agents[keyFor("home")].activeContextId = undefined;
		const pending = deferred();
		rpcReply = async (_request, body) => { await pending.promise; return reply(body, "Late old welcome"); };
		app = render(<ChatApp options={options} />);
		await vi.waitFor(() => expect(rpc).toHaveLength(1));
		await enter("/workspace team"); await ready("team");
		pending.resolve();
		await act(async () => { await new Promise((resolve) => setTimeout(resolve, 25)); });
		expect(rpc[0].request.signal.aborted).toBe(true);
		expect(state.saves).toHaveLength(0);
		expect(app.frames.join("\n")).not.toContain("Late old welcome");
	});
	it("does not apply a pending workspace choice after environment changes", async () => {
		app = render(<ChatApp options={options} />); await ready();
		const pending = deferred();
		holdMembership = () => pending.promise;
		await enter("/workspace team");
		await vi.waitFor(() => expect(membershipCalls).toBe(2));
		await enter("/env staging");
		pending.resolve();
		await vi.waitFor(() => expect(state.settings.selectedEnvironment).toBe("staging"));
		await act(async () => { await new Promise((resolve) => setTimeout(resolve, 25)); });
		expect(savedChoice()).toBe("home");
		expect(state.loads).not.toContain(keyFor("team"));
	});
	it("keeps a verified context when its local transcript is missing", async () => {
		welcome = true; state.sessions.delete(keyFor("home"));
		app = render(<ChatApp options={options} />); await ready();
		expect(rpc).toHaveLength(0);
		expect(app.frames.join("\n")).toContain("saved history could not be loaded");
		await enter("Continue");
		await vi.waitFor(() => expect(rpc).toHaveLength(1));
		expect(rpc[0].body.params.message.contextId).toBe("home-context");
	});
});

it("supports the workspace picker and revalidates the chosen membership", async () => {
	app = render(<ChatApp options={options} />); await ready();
	await enter("/workspace");
	await vi.waitFor(() => expect(app!.lastFrame()).toContain("Terminal Research"));
	await act(async () => app!.stdin.write("3"));
	await ready("team");
	expect(savedChoice()).toBe("team");
	expect(membershipCalls).toBe(3);
});

it("opens a picker for a revoked saved workspace without sending a greeting elsewhere", async () => {
	state.settings.environments.production.registryAccounts![accountScope].workspaceId = "revoked";
	app = render(<ChatApp options={options} />);
	await vi.waitFor(() => expect(app!.lastFrame()).toContain("Terminal Research"));
	expect(rpc).toHaveLength(0); expect(catalogs).toEqual([]);
	await act(async () => app!.stdin.write("3"));
	await ready("team");
});

it("releases a timed-out selection and ignores its later result", async () => {
	app = render(<ChatApp options={options} />); await ready();
	const pending = deferred();
	holdMembership = () => pending.promise;
	const timers = vi.spyOn(globalThis, "setTimeout");
	await enter("/workspace team");
	await vi.waitFor(() => expect(membershipCalls).toBe(2));
	const expiration = timers.mock.calls.find(([, duration]) => duration === 30_000)![0];
	await act(async () => { (expiration as () => void)(); });
	expect(app.frames.join("\n")).toContain("selection timed out");
	holdMembership = undefined;
	await enter("/workspace other"); await ready("other");
	pending.resolve();
	await act(async () => { await new Promise((resolve) => setTimeout(resolve, 25)); });
	expect(savedChoice()).toBe("other");
	expect(state.loads).not.toContain(keyFor("team"));
});

it("ignores pending selection results after unmount", async () => {
	app = render(<ChatApp options={options} />); await ready();
	const pending = deferred();
	holdMembership = () => pending.promise;
	await enter("/workspace team");
	await vi.waitFor(() => expect(membershipCalls).toBe(2));
	app.unmount(); app = undefined;
	pending.resolve();
	await new Promise((resolve) => setTimeout(resolve, 25));
	expect(savedChoice()).toBe("home");
	expect(state.loads).not.toContain(keyFor("team"));
});

it("releases the guard after a connection failure and allows another workspace", async () => {
	app = render(<ChatApp options={options} />); await ready();
	holdCard = async (count) => { if (count === 4) throw new Error("Connection unavailable"); };
	await enter("/workspace team");
	await vi.waitFor(() => expect(app!.frames.join("\n")).toContain("Connection failed"));
	holdCard = undefined;
	await enter("/workspace other"); await ready("other");
	expect(savedChoice()).toBe("other");
});

it("invalidates pending choices when login changes the authenticated account", async () => {
	app = render(<ChatApp options={options} />); await ready();
	const pending = deferred();
	holdMembership = () => pending.promise;
	await enter("/workspace team");
	await vi.waitFor(() => expect(membershipCalls).toBe(2));
	holdMembership = undefined;
	state.accountId = "another-user";
	state.token = 'header.' + Buffer.from(JSON.stringify({ iss: "issuer", sub: state.accountId })).toString("base64url") + '.test';
	await enter("/login");
	const newScope = registryAccountScope("production", state.accountId);
	await vi.waitFor(() => expect(state.settings.environments.production.registryAccounts?.[newScope]?.workspaceId).toBe("home"));
	pending.resolve();
	await act(async () => { await new Promise((resolve) => setTimeout(resolve, 25)); });
	expect(savedChoice()).toBe("home");
	expect(state.loads).toEqual([keyFor("home")]);
	expect(state.settings.environments.production.selectedAgentKey).toBeUndefined();
});
