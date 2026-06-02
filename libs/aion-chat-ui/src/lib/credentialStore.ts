import { spawn } from "node:child_process";

import type { AionEnvironmentId } from "./environment.js";

const SERVICE_NAME = "aion-chat";

/**
 * JSON-encoded argv for the Python credential helper used by `aion chat`.
 *
 * Direct npm/npx chat launches leave this unset and use the installed Node
 * keyring package. The Python launcher sets it so the bundled Node UI can
 * delegate WorkOS refresh-token reads and writes to Python `keyring`, avoiding
 * npm-native keyring artifacts inside the Python package.
 */
export const AION_CHAT_CREDENTIAL_HELPER_ENV = "AION_CHAT_CREDENTIAL_HELPER";

export interface CredentialStore {
	getRefreshToken(environmentId: AionEnvironmentId): Promise<string | undefined>;
	setRefreshToken(
		environmentId: AionEnvironmentId,
		refreshToken: string
	): Promise<void>;
	deleteRefreshToken(environmentId: AionEnvironmentId): Promise<void>;
}

interface CredentialHelperRequest {
	action: "get" | "set" | "delete";
	environmentId: AionEnvironmentId;
	refreshToken?: string;
}

interface CredentialHelperResponse {
	refreshToken?: string | null;
}

function accountName(environmentId: AionEnvironmentId): string {
	return `${environmentId}:user`;
}

function credentialError(action: string, error: unknown): Error {
	const message = error instanceof Error ? error.message : String(error);
	return new Error(
		`Unable to ${action} Aion login credentials from the operating system keychain: ${message}`
	);
}

async function loadKeyring(): Promise<typeof import("@napi-rs/keyring")> {
	const packageName = "@napi-rs/keyring";
	return import(packageName);
}

function parseCredentialHelperCommand(value: string): string[] {
	let parsed: unknown;
	try {
		parsed = JSON.parse(value);
	} catch (error) {
		throw new Error(
			`${AION_CHAT_CREDENTIAL_HELPER_ENV} must be a JSON-encoded argv array.`
		);
	}

	if (
		!Array.isArray(parsed) ||
		parsed.length === 0 ||
		!parsed.every((item): item is string => typeof item === "string" && item.length > 0)
	) {
		throw new Error(
			`${AION_CHAT_CREDENTIAL_HELPER_ENV} must be a non-empty JSON array of strings.`
		);
	}
	return parsed;
}

function parseCredentialHelperResponse(value: string): CredentialHelperResponse {
	if (!value.trim()) {
		return {};
	}
	let parsed: CredentialHelperResponse;
	try {
		parsed = JSON.parse(value) as CredentialHelperResponse;
	} catch {
		throw new Error("Credential helper returned invalid JSON.");
	}
	if (
		parsed.refreshToken !== undefined &&
		parsed.refreshToken !== null &&
		typeof parsed.refreshToken !== "string"
	) {
		throw new Error("Credential helper returned an invalid refreshToken value.");
	}
	return parsed;
}

export class HelperCredentialStore implements CredentialStore {
	private readonly command: string[];

	constructor(commandValue: string) {
		this.command = parseCredentialHelperCommand(commandValue);
	}

	async getRefreshToken(environmentId: AionEnvironmentId): Promise<string | undefined> {
		const response = await this.request({ action: "get", environmentId });
		return response.refreshToken?.trim() || undefined;
	}

	async setRefreshToken(
		environmentId: AionEnvironmentId,
		refreshToken: string
	): Promise<void> {
		await this.request({ action: "set", environmentId, refreshToken });
	}

	async deleteRefreshToken(environmentId: AionEnvironmentId): Promise<void> {
		await this.request({ action: "delete", environmentId });
	}

	private request(request: CredentialHelperRequest): Promise<CredentialHelperResponse> {
		const [executable, ...args] = this.command;
		return new Promise((resolve, reject) => {
			const child = spawn(executable, args, {
				stdio: ["pipe", "pipe", "pipe"]
			});
			if (!child.stdin || !child.stdout || !child.stderr) {
				reject(new Error("Credential helper did not expose standard streams."));
				return;
			}
			let stdout = "";
			let stderr = "";

			child.stdout.setEncoding("utf8");
			child.stdout.on("data", (chunk: string) => {
				stdout += chunk;
			});
			child.stderr.setEncoding("utf8");
			child.stderr.on("data", (chunk: string) => {
				stderr += chunk;
			});
			child.on("error", reject);
			child.on("close", (code) => {
				if (code !== 0) {
					reject(
						new Error(
							stderr.trim() || `Credential helper exited with status ${code ?? "unknown"}.`
						)
					);
					return;
				}
				try {
					resolve(parseCredentialHelperResponse(stdout));
				} catch (error) {
					reject(error);
				}
			});
			child.stdin.end(JSON.stringify(request));
		});
	}
}

export class KeyringCredentialStore implements CredentialStore {
	async getRefreshToken(environmentId: AionEnvironmentId): Promise<string | undefined> {
		try {
			const { AsyncEntry } = await loadKeyring();
			const token = await new AsyncEntry(
				SERVICE_NAME,
				accountName(environmentId)
			).getPassword();
			return token ?? undefined;
		} catch (error) {
			throw credentialError("read", error);
		}
	}

	async setRefreshToken(
		environmentId: AionEnvironmentId,
		refreshToken: string
	): Promise<void> {
		try {
			const { AsyncEntry } = await loadKeyring();
			await new AsyncEntry(SERVICE_NAME, accountName(environmentId)).setPassword(
				refreshToken
			);
		} catch (error) {
			throw credentialError("store", error);
		}
	}

	async deleteRefreshToken(environmentId: AionEnvironmentId): Promise<void> {
		try {
			const { AsyncEntry } = await loadKeyring();
			await new AsyncEntry(
				SERVICE_NAME,
				accountName(environmentId)
			).deletePassword();
		} catch (error) {
			throw credentialError("delete", error);
		}
	}
}

export const keyringCredentialStore = new KeyringCredentialStore();

export function createDefaultCredentialStore(
	env: NodeJS.ProcessEnv = process.env
): CredentialStore {
	const helperCommand = env[AION_CHAT_CREDENTIAL_HELPER_ENV];
	if (helperCommand) {
		return new HelperCredentialStore(helperCommand);
	}
	return keyringCredentialStore;
}

/**
 * Default credential store for chat UI authentication.
 *
 * Direct npm launches leave AION_CHAT_CREDENTIAL_HELPER unset and use the Node
 * keyring dependency installed by npm. Python launches set that variable to a
 * JSON-encoded helper argv so the Python package can own OS keychain access
 * without requiring npm-only native keyring dependencies inside the Poetry venv.
 */
export const defaultCredentialStore = createDefaultCredentialStore();
