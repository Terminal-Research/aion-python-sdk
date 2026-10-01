// Controlled receiver for the packaged CLI. No request leaves this process and
// no real token is minted. This tests delivery, not SDK server authentication.
import { appendFileSync } from "node:fs";

globalThis.fetch = async (input, init) => {
  const request = new Request(input, init);
  const url = new URL(request.url);
  appendFileSync(process.env.AION_CHAT_TEST_REQUESTS, JSON.stringify({
    url: request.url, authorization: request.headers.get("Authorization")
  }) + "\n");
  if (request.url === "http://localhost:8080/auth/anonymous-sessions") {
    return Response.json({ sessionId: "00000000-0000-4000-8000-000000000001",
      token: "fixture-guest-bearer", expiresAt: new Date(Date.now() + 30 * 86400000).toISOString() });
  }
  if (url.origin !== "http://localhost:8000") throw new Error("Unexpected external request in CLI fixture");
  if (request.headers.get("Authorization") !== "Bearer fixture-guest-bearer") {
    return new Response(null, { status: 401 });
  }
  if (url.pathname.endsWith("manifest.json")) {
    return Response.json({ endpoints: { demo: "/agents/demo" } });
  }
  if (url.pathname.endsWith("agent-card.json")) {
    return Response.json({ name: "Fixture agent", description: "Controlled CLI receiver", version: "1",
      supportedInterfaces: [{ url: "http://localhost:8000/agents/demo/", protocolBinding: "JSONRPC", protocolVersion: "1.0" }],
      capabilities: {}, skills: [], defaultInputModes: ["text/plain"], defaultOutputModes: ["text/plain"] });
  }
  const body = await request.json();
  return Response.json({ jsonrpc: "2.0", id: body.id, result: { message: {
    messageId: "reply", contextId: "context", role: "ROLE_AGENT", parts: [{ text: "Packaged guest reply" }]
  } } });
};
