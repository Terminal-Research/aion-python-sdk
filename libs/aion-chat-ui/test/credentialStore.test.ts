import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
	AION_CHAT_CREDENTIAL_HELPER_ENV,
	HelperCredentialStore,
	createDefaultCredentialStore,
	keyringCredentialStore
} from "../src/lib/credentialStore.js";

async function createHelperScript(): Promise<string> {
	const dir = await mkdtemp(path.join(tmpdir(), "aion-chat-credential-helper-"));
	const script = path.join(dir, "helper.mjs");
	await writeFile(
		script,
		`
const chunks = [];
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => chunks.push(chunk));
process.stdin.on("end", () => {
  const request = JSON.parse(chunks.join(""));
  if (request.action === "get") {
    process.stdout.write(JSON.stringify({ refreshToken: "stored-" + request.environmentId }));
    return;
  }
  if (request.action === "set" && request.refreshToken === "new-refresh-token") {
    process.stdout.write(JSON.stringify({}));
    return;
  }
  if (request.action === "delete") {
    process.stdout.write(JSON.stringify({}));
    return;
  }
  process.stderr.write("unexpected helper request");
  process.exitCode = 1;
});
`
	);
	return script;
}

describe("HelperCredentialStore", () => {
	it("delegates refresh-token operations to the configured helper command", async () => {
		const helperScript = await createHelperScript();
		const store = new HelperCredentialStore(
			JSON.stringify([process.execPath, helperScript])
		);

		await expect(store.getRefreshToken("development")).resolves.toBe(
			"stored-development"
		);
		await expect(
			store.setRefreshToken("development", "new-refresh-token")
		).resolves.toBeUndefined();
		await expect(store.deleteRefreshToken("development")).resolves.toBeUndefined();
	});

	it("uses the keyring store when no helper environment variable is set", () => {
		expect(createDefaultCredentialStore({})).toBe(keyringCredentialStore);
	});

	it("uses the helper-backed store when the helper environment variable is set", () => {
		const store = createDefaultCredentialStore({
			[AION_CHAT_CREDENTIAL_HELPER_ENV]: JSON.stringify(["python", "-m", "helper"])
		});

		expect(store).toBeInstanceOf(HelperCredentialStore);
	});
});
