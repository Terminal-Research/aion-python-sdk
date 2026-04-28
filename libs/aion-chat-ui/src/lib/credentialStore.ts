import type { AionEnvironmentId } from "./environment.js";

const SERVICE_NAME = "aion-chat";

export interface CredentialStore {
	getRefreshToken(environmentId: AionEnvironmentId): Promise<string | undefined>;
	setRefreshToken(
		environmentId: AionEnvironmentId,
		refreshToken: string
	): Promise<void>;
	deleteRefreshToken(environmentId: AionEnvironmentId): Promise<void>;
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
