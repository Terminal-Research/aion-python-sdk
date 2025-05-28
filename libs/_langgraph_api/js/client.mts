/// <reference types="./global.d.ts" />

import { z } from "zod";
import { Context, Hono } from "hono";
import { serve } from "@hono/node-server";
import { zValidator } from "@hono/zod-validator";
import { streamSSE, stream } from "hono/streaming";
import { HTTPException } from "hono/http-exception";
import { fetch } from "undici";
import pRetry from "p-retry";
import {
  BaseStore,
  Item,
  Operation,
  Command,
  OperationResults,
  type Checkpoint,
  type CheckpointMetadata,
  type CheckpointTuple,
  type CompiledGraph,
} from "@langchain/langgraph";
import {
  BaseCheckpointSaver,
  type ChannelVersions,
  type ChannelProtocol,
} from "@langchain/langgraph-checkpoint";
import { createHash } from "node:crypto";
import * as fs from "node:fs/promises";
import * as path from "node:path";
import { serialiseAsDict, serializeError } from "./src/utils/serde.mjs";
import * as importMap from "./src/utils/importMap.mjs";

import { createLogger, format, transports } from "winston";

import { load } from "@langchain/core/load";
import { BaseMessageChunk, isBaseMessage } from "@langchain/core/messages";
import type { PyItem, PyResult } from "./src/utils/pythonSchemas.mts";
import type { RunnableConfig } from "@langchain/core/runnables";
import {
  runGraphSchemaWorker,
  GraphSchema,
  resolveGraph,
  GraphSpec,
  filterValidGraphSpecs,
} from "./src/graph.mts";
import { asyncExitHook, gracefulExit } from "exit-hook";
import { awaitAllCallbacks } from "@langchain/core/callbacks/promises";

const logger = createLogger({
  level: "debug",
  format: format.combine(
    format.errors({ stack: true }),
    format.timestamp(),
    format.json(),
    format.printf((info) => {
      const { timestamp, level, message, ...rest } = info;

      let event;
      if (typeof message === "string") {
        event = message;
      } else {
        event = JSON.stringify(message);
      }

      if (rest.stack) {
        rest.message = event;
        event = rest.stack;
      }

      return JSON.stringify({ timestamp, level, event, ...rest });
    })
  ),
  transports: [
    new transports.Console({
      handleExceptions: true,
      handleRejections: true,
    }),
  ],
});

let GRAPH_SCHEMA: Record<string, Record<string, GraphSchema> | false> = {};
const GRAPH_RESOLVED: Record<string, CompiledGraph<string>> = {};
const GRAPH_SPEC: Record<string, GraphSpec> = {};

function getGraph(graphId: string) {
  if (!GRAPH_RESOLVED[graphId])
    throw new HTTPException(404, { message: `Graph "${graphId}" not found` });
  return GRAPH_RESOLVED[graphId];
}

async function getOrExtractSchema(graphId: string) {
  if (!(graphId in GRAPH_SPEC)) {
    throw new Error(`Spec for ${graphId} not found`);
  }

  if (!GRAPH_SCHEMA[graphId]) {
    // This is only set during build phase
    if (GRAPH_SCHEMA[graphId] === false) {
      throw new Error(`Failed to locate schema for "${graphId}"`);
    }

    try {
      const timer = logger.startTimer();

      let timeoutMs: number | undefined = undefined;
      try {
        timeoutMs = Number.parseInt(
          process.env.LANGGRAPH_SCHEMA_RESOLVE_TIMEOUT_MS || "30000",
          10
        );
        if (Number.isNaN(timeoutMs) || timeoutMs <= 0) timeoutMs = undefined;
      } catch {
        // ignore
      }

      GRAPH_SCHEMA[graphId] = await runGraphSchemaWorker(GRAPH_SPEC[graphId], {
        timeoutMs,
      });
      timer.done({ message: `Extracting schema for ${graphId} finished` });
    } catch (error) {
      throw new Error(`Failed to extract schema for "${graphId}": ${error}`);
    }
  }

  return GRAPH_SCHEMA[graphId];
}

const GRAPH_PORT = 5556;
const REMOTE_PORT = 5555;

const RunnableConfigSchema = z.object({
  tags: z.array(z.string()).optional(),
  metadata: z.record(z.unknown()).optional(),
  run_name: z.string().optional(),
  max_concurrency: z.number().optional(),
  recursion_limit: z.number().optional(),
  configurable: z.record(z.unknown()).optional(),
  run_id: z.string().uuid().optional(),
});

const getRunnableConfig = (
  userConfig: z.infer<typeof RunnableConfigSchema> | null | undefined
) => {
  if (!userConfig) return {};
  return {
    configurable: userConfig.configurable,
    tags: userConfig.tags,
    metadata: userConfig.metadata,
    runName: userConfig.run_name,
    maxConcurrency: userConfig.max_concurrency,
    recursionLimit: userConfig.recursion_limit,
    runId: userConfig.run_id,
  };
};

function tryFetch(...args: Parameters<typeof fetch>) {
  return pRetry(
    async () => {
      const response = await fetch(...args).catch((error) => {
        throw new Error(`${args[0]} connecfailed: ${error}`);
      });

      if (!response.ok) {
        let errorMessage = `${args[0]} failed: HTTP ${response.status}`;
        try {
          errorMessage += `: ${await response.text()}`;
        } catch {}
        throw new Error(errorMessage);
      }

      return response;
    },
    {
      retries: 3,
      factor: 2,
      minTimeout: 1000,
      onFailedAttempt: (error) => void logger.error(error),
    }
  );
}

async function sendRecv<T = any>(
  method: `${"checkpointer" | "store"}_${string}`,
  data: unknown
): Promise<T> {
  const res = await tryFetch(`http://localhost:${REMOTE_PORT}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });

  return (await load(await res.text(), {
    importMap,
    optionalImportEntrypoints: [],
    optionalImportsMap: {},
    secretsMap: {},
  })) as T;
}

const HEARTBEAT_MS = 5_000;
const handleInvoke = <T extends z.ZodType<any>>(
  name: string,
  _schema: T,
  handler: (rawPayload: z.infer<T>) => Promise<any>
) => {
  return async (c: Context<any, any, { in: z.infer<T>; out: any }>) => {
    const graphId = c.req.param("graphId");
    const body = c.req.valid("json") as any;

    // send heartbeat every HEARTBEAT_INTERVAL_MS
    // to prevent connection from timing out
    c.header("Content-Type", "application/json");
    return stream(c, async (stream) => {
      let resolved: Promise<any> = Promise.resolve();
      const enqueueWrite = (payload: Uint8Array | string) => {
        resolved = stream.write(payload);
        return resolved;
      };

      // orjson.loads(...) does ignore the
      // whitespace prefix, so we can use that
      // as a heartbeat
      let interval = setInterval(() => enqueueWrite(" "), HEARTBEAT_MS);

      const response = JSON.stringify(
        await handler({ graph_id: graphId, ...body })
      );

      clearInterval(interval);
      await enqueueWrite(response);
    });
  };
};

const handleStream = <T extends z.ZodType<any>>(
  name: string,
  _schema: T,
  handler: (rawPayload: z.infer<T>) => AsyncGenerator<any, void, unknown>
) => {
  return (c: Context<any, any, { in: z.infer<T>; out: any }>) => {
    const graphId = c.req.param("graphId");
    const body = c.req.valid("json") as any;
    return streamSSE(c, async (stream) => {
      let resolve: Promise<any> = Promise.resolve();
      let timer: NodeJS.Timeout | undefined;
      const sendHeartbeat = () => {
        clearTimeout(timer);
        resolve = stream.writeln(": heartbeat");
        timer = setTimeout(sendHeartbeat, HEARTBEAT_MS);
        return resolve;
      };

      const sendSSE = (event: string, data: unknown) => {
        clearTimeout(timer);
        resolve = stream.writeSSE({ data: serialiseAsDict(data), event });
        timer = setTimeout(sendHeartbeat, HEARTBEAT_MS);
        return resolve;
      };

      try {
        for await (const data of handler({ graph_id: graphId, ...body })) {
          await sendSSE(name, data);
        }
      } catch (error) {
        // Still print out the error, as the stack
        // trace is not carried over in Python
        logger.error(error);
        await sendSSE("error", serializeError(error));
      }

      clearTimeout(timer);
      await resolve;
    });
  };
};

class RemoteCheckpointer extends BaseCheckpointSaver<number | string> {
  async getTuple(config: RunnableConfig): Promise<CheckpointTuple | undefined> {
    const result = await sendRecv("checkpointer_get_tuple", { config });

    if (!result) return undefined;
    return {
      checkpoint: result.checkpoint,
      config: result.config,
      metadata: result.metadata,
      parentConfig: result.parent_config,
      pendingWrites: result.pending_writes,
    };
  }

  async *list(
    config: RunnableConfig,
    options?: {
      limit?: number;
      before?: RunnableConfig;
      filter?: Record<string, any>;
    }
  ): AsyncGenerator<CheckpointTuple> {
    const result = await sendRecv("checkpointer_list", { config, ...options });

    for (const item of result) {
      yield {
        checkpoint: item.checkpoint,
        config: item.config,
        metadata: item.metadata,
        parentConfig: item.parent_config,
        pendingWrites: item.pending_writes,
      };
    }
  }

  async put(
    config: RunnableConfig,
    checkpoint: Checkpoint,
    metadata: CheckpointMetadata,
    newVersions: ChannelVersions
  ): Promise<RunnableConfig> {
    return await sendRecv<RunnableConfig>("checkpointer_put", {
      config,
      checkpoint,
      metadata,
      new_versions: newVersions,
    });
  }

  async putWrites(
    config: RunnableConfig,
    writes: [string, unknown][],
    taskId: string
  ): Promise<void> {
    await sendRecv("checkpointer_put_writes", { config, writes, taskId });
  }

  getNextVersion(
    current: number | string | undefined,
    _channel: ChannelProtocol
  ): string {
    let currentVersion = 0;

    if (current == null) {
      currentVersion = 0;
    } else if (typeof current === "number") {
      currentVersion = current;
    } else if (typeof current === "string") {
      currentVersion = Number.parseInt(current.split(".")[0], 10);
    }

    const nextVersion = String(currentVersion + 1).padStart(32, "0");
    try {
      const hash = createHash("md5")
        .update(serialiseAsDict(_channel.checkpoint()))
        .digest("hex");
      return `${nextVersion}.${hash}`;
    } catch {}

    return nextVersion;
  }
}

function camelToSnake(operation: Operation) {
  const snakeCaseKeys = (obj: Record<string, any>): Record<string, any> => {
    return Object.fromEntries(
      Object.entries(obj).map(([key, value]) => {
        const snakeKey = key.replace(
          /[A-Z]/g,
          (letter) => `_${letter.toLowerCase()}`
        );
        if (
          typeof value === "object" &&
          value !== null &&
          !Array.isArray(value)
        ) {
          return [snakeKey, snakeCaseKeys(value)];
        }
        return [snakeKey, value];
      })
    );
  };

  if ("namespace" in operation && "key" in operation) {
    return {
      namespace: operation.namespace,
      key: operation.key,
      ...("value" in operation ? { value: operation.value } : {}),
    };
  } else if ("namespacePrefix" in operation) {
    return {
      namespace_prefix: operation.namespacePrefix,
      filter: operation.filter,
      limit: operation.limit,
      offset: operation.offset,
    };
  } else if ("matchConditions" in operation) {
    return {
      match_conditions: operation.matchConditions?.map((condition) => ({
        match_type: condition.matchType,
        path: condition.path,
      })),
      max_depth: operation.maxDepth,
      limit: operation.limit,
      offset: operation.offset,
    };
  }

  return snakeCaseKeys(operation) as Operation;
}

function pyItemToJs(item?: PyItem): Item | undefined {
  if (!item) {
    return undefined;
  }
  return {
    namespace: item.namespace,
    key: item.key,
    value: item.value,
    createdAt: item.created_at,
    updatedAt: item.updated_at,
  };
}

export class RemoteStore extends BaseStore {
  async batch<Op extends Operation[]>(
    operations: Op
  ): Promise<OperationResults<Op>> {
    const results = await sendRecv<PyResult[]>("store_batch", {
      operations: operations.map(camelToSnake),
    });

    return results.map((result) => {
      if (Array.isArray(result)) {
        return result.map((item) => pyItemToJs(item));
      } else if (
        result &&
        typeof result === "object" &&
        "value" in result &&
        "key" in result
      ) {
        return pyItemToJs(result);
      }
      return result;
    }) as OperationResults<Op>;
  }

  async get(namespace: string[], key: string): Promise<Item | null> {
    return await sendRecv<Item | null>("store_get", {
      namespace: namespace.join("."),
      key,
    });
  }

  async search(
    namespacePrefix: string[],
    options?: {
      filter?: Record<string, any>;
      limit?: number;
      offset?: number;
    }
  ): Promise<Item[]> {
    return await sendRecv<Item[]>("store_search", {
      namespace_prefix: namespacePrefix,
      ...options,
    });
  }

  async put(
    namespace: string[],
    key: string,
    value: Record<string, any>
  ): Promise<void> {
    await sendRecv("store_put", { namespace, key, value });
  }

  async delete(namespace: string[], key: string): Promise<void> {
    await sendRecv("store_delete", { namespace, key });
  }

  async listNamespaces(options: {
    prefix?: string[];
    suffix?: string[];
    maxDepth?: number;
    limit?: number;
    offset?: number;
  }): Promise<string[][]> {
    const data = await sendRecv<{ namespaces: string[][] }>(
      "store_list_namespaces",
      { max_depth: options?.maxDepth, ...options }
    );
    return data.namespaces;
  }
}

const StreamModeSchema = z.union([
  z.literal("updates"),
  z.literal("debug"),
  z.literal("values"),
  z.literal("custom"),
]);

const ExtraStreamModeSchema = z.union([
  StreamModeSchema,
  z.literal("messages"),
  z.literal("messages-tuple"),
]);

const StreamEventsPayload = z.object({
  graph_id: z.string(),
  input: z.unknown(),
  command: z.object({ resume: z.unknown() }).nullish(),
  stream_mode: z
    .union([ExtraStreamModeSchema, z.array(ExtraStreamModeSchema)])
    .optional(),
  config: RunnableConfigSchema.nullish(),
  interrupt_before: z.union([z.array(z.string()), z.literal("*")]).nullish(),
  interrupt_after: z.union([z.array(z.string()), z.literal("*")]).nullish(),
  subgraphs: z.boolean().optional(),
});

async function* streamEventsRequest(
  rawPayload: z.infer<typeof StreamEventsPayload>
) {
  const { graph_id: graphId, ...payload } = rawPayload;
  const graph = getGraph(graphId);

  const input = payload.command ? new Command(payload.command) : payload.input;

  const userStreamMode =
    payload.stream_mode == null
      ? []
      : Array.isArray(payload.stream_mode)
        ? payload.stream_mode
        : [payload.stream_mode];

  const graphStreamMode: Set<
    "updates" | "debug" | "values" | "messages" | "custom"
  > = new Set();
  if (payload.stream_mode) {
    for (const mode of userStreamMode) {
      if (mode === "messages") {
        graphStreamMode.add("values");
      } else if (mode === "messages-tuple") {
        graphStreamMode.add("messages");
      } else {
        graphStreamMode.add(mode);
      }
    }
  }

  const config = getRunnableConfig(payload.config);

  const messages: Record<string, BaseMessageChunk> = {};
  const completedIds = new Set<string>();

  let interruptBefore: typeof payload.interrupt_before =
    payload.interrupt_before ?? undefined;

  if (Array.isArray(interruptBefore) && interruptBefore.length === 0)
    interruptBefore = undefined;

  let interruptAfter: typeof payload.interrupt_after =
    payload.interrupt_after ?? undefined;

  if (Array.isArray(interruptAfter) && interruptAfter.length === 0)
    interruptAfter = undefined;

  const streamMode = [...graphStreamMode];

  for await (const data of graph.streamEvents(input, {
    ...config,
    version: "v2",
    streamMode,
    subgraphs: payload.subgraphs,
    interruptBefore,
    interruptAfter,
  })) {
    // TODO: upstream this fix to LangGraphJS
    if (streamMode.length === 1 && !Array.isArray(data.data.chunk)) {
      data.data.chunk = [streamMode[0], data.data.chunk];
    }

    if (payload.subgraphs) {
      if (Array.isArray(data.data.chunk) && data.data.chunk.length === 2) {
        data.data.chunk = [[], ...data.data.chunk];
      }
    }

    yield data;

    if (userStreamMode.includes("messages")) {
      if (data.event === "on_chain_stream" && data.run_id === config.runId) {
        const newMessages: Array<BaseMessageChunk> = [];
        const [_, chunk]: [string, any] = data.data.chunk;

        let chunkMessages: Array<BaseMessageChunk> = [];
        if (
          typeof chunk === "object" &&
          chunk != null &&
          "messages" in chunk &&
          !isBaseMessage(chunk)
        ) {
          chunkMessages = chunk?.messages;
        }

        if (!Array.isArray(chunkMessages)) {
          chunkMessages = [chunkMessages];
        }

        for (const message of chunkMessages) {
          if (!message.id || completedIds.has(message.id)) continue;
          completedIds.add(message.id);
          newMessages.push(message);
        }

        if (newMessages.length > 0) {
          yield {
            event: "on_custom_event",
            name: "messages/complete",
            data: newMessages,
          };
        }
      } else if (
        data.event === "on_chat_model_stream" &&
        !data.tags?.includes("nostream")
      ) {
        const message: BaseMessageChunk = data.data.chunk;

        if (!message.id) continue;

        if (messages[message.id] == null) {
          messages[message.id] = message;
          yield {
            event: "on_custom_event",
            name: "messages/metadata",
            data: { [message.id]: { metadata: data.metadata } },
          };
        } else {
          messages[message.id] = messages[message.id].concat(message);
        }

        yield {
          event: "on_custom_event",
          name: "messages/partial",
          data: [messages[message.id]],
        };
      }
    }
  }
}

const GetGraphPayload = z.object({
  graph_id: z.string(),
  config: RunnableConfigSchema.nullish(),
  xray: z.union([z.number(), z.boolean()]).nullish(),
});

async function getGraphRequest(rawPayload: z.infer<typeof GetGraphPayload>) {
  const { graph_id: graphId, ...payload } = rawPayload;
  const graph = getGraph(graphId);

  const drawable = await graph.getGraphAsync({
    ...getRunnableConfig(payload.config),
    xray: payload.xray ?? undefined,
  });
  return drawable.toJSON();
}

const GetSubgraphsPayload = z.object({
  graph_id: z.string(),
  namespace: z.string().nullish(),
  recurse: z.boolean().nullish(),
});

async function getSubgraphsRequest(
  rawPayload: z.infer<typeof GetSubgraphsPayload>
) {
  const { graph_id: graphId, ...payload } = rawPayload;
  const graph = getGraph(graphId);
  const result: Array<[name: string, Record<string, any>]> = [];

  const graphSchema = await getOrExtractSchema(graphId);
  const rootGraphId = Object.keys(graphSchema).find((i) => !i.includes("|"));

  if (!rootGraphId) throw new Error("Failed to find root graph");

  for await (const [name] of graph.getSubgraphsAsync(
    payload.namespace ?? undefined,
    payload.recurse ?? undefined
  )) {
    const schema =
      graphSchema[`${rootGraphId}|${name}`] || graphSchema[rootGraphId];
    result.push([name, schema]);
  }

  // TODO: make this a stream
  return Object.fromEntries(result);
}

const GetStatePayload = z.object({
  graph_id: z.string(),
  config: RunnableConfigSchema,
  subgraphs: z.boolean().nullish(),
});

async function getStateRequest(rawPayload: z.infer<typeof GetStatePayload>) {
  const { graph_id: graphId, ...payload } = rawPayload;
  const graph = getGraph(graphId);

  const state = await graph.getState(getRunnableConfig(payload.config), {
    subgraphs: payload.subgraphs ?? undefined,
  });

  // TODO: just send the JSON directly, don't ser/de twice
  return JSON.parse(serialiseAsDict(state));
}

const UpdateStatePayload = z.object({
  graph_id: z.string(),
  config: RunnableConfigSchema,
  values: z.unknown(),
  as_node: z.string().nullish(),
});

async function updateStateRequest(
  rawPayload: z.infer<typeof UpdateStatePayload>
) {
  const { graph_id: graphId, ...payload } = rawPayload;
  const graph = getGraph(graphId);

  const config = await graph.updateState(
    getRunnableConfig(payload.config),
    payload.values,
    payload.as_node ?? undefined
  );

  return config;
}

const GetSchemaPayload = z.object({ graph_id: z.string() });

async function getSchemaRequest(payload: z.infer<typeof GetSchemaPayload>) {
  const { graph_id: graphId } = payload;
  const schemas = await getOrExtractSchema(graphId);
  const rootGraphId = Object.keys(schemas).find((i) => !i.includes("|"));
  if (!rootGraphId) {
    throw new Error("Failed to find root graph");
  }
  return schemas[rootGraphId];
}

const GetStateHistoryPayload = z.object({
  graph_id: z.string(),
  config: RunnableConfigSchema,
  limit: z.number().nullish(),
  before: RunnableConfigSchema.nullish(),
  filter: z.record(z.unknown()).nullish(),
});

async function* getStateHistoryRequest(
  rawPayload: z.infer<typeof GetStateHistoryPayload>
) {
  const { graph_id: graphId, ...payload } = rawPayload;
  const graph = getGraph(graphId);

  for await (const item of graph.getStateHistory(
    getRunnableConfig(payload.config),
    {
      limit: payload.limit ?? undefined,
      before: payload.before ? getRunnableConfig(payload.before) : undefined,
      filter: payload.filter ?? undefined,
    }
  )) {
    yield item;
  }
}

const __dirname = new URL(".", import.meta.url).pathname;

async function main() {
  const app = new Hono();
  const checkpointer = new RemoteCheckpointer();
  const store = new RemoteStore();

  const specs = filterValidGraphSpecs(
    z.record(z.string()).parse(JSON.parse(process.env.LANGSERVE_GRAPHS ?? "{}"))
  );

  if (!process.argv.includes("--skip-schema-cache")) {
    try {
      GRAPH_SCHEMA = JSON.parse(
        await fs.readFile(path.resolve(__dirname, "client.schemas.json"), {
          encoding: "utf-8",
        })
      );
    } catch {
      // pass
    }
  }

  await Promise.all(
    specs.map(async ([graphId, rawSpec]) => {
      logger.info(`Resolving graph ${graphId}`);
      const { resolved, ...spec } = await resolveGraph(rawSpec);

      // TODO: make sure the types do not need to be upfront
      // @ts-expect-error Overriding checkpointer with different value type
      resolved.checkpointer = checkpointer;
      resolved.store = store;

      // registering the graph runtime
      GRAPH_RESOLVED[graphId] = resolved;
      GRAPH_SPEC[graphId] = spec;
    })
  );

  app.post(
    "/:graphId/streamEvents",
    zValidator("json", StreamEventsPayload),
    handleStream("streamEvents", StreamEventsPayload, streamEventsRequest)
  );

  app.post(
    "/:graphId/getGraph",
    zValidator("json", GetGraphPayload),
    handleInvoke("getGraph", GetGraphPayload, getGraphRequest)
  );

  app.post(
    "/:graphId/getSubgraphs",
    zValidator("json", GetSubgraphsPayload),
    handleInvoke("getSubgraphs", GetSubgraphsPayload, getSubgraphsRequest)
  );

  app.post(
    "/:graphId/getState",
    zValidator("json", GetStatePayload),
    handleInvoke("getState", GetStatePayload, getStateRequest)
  );

  app.post(
    "/:graphId/updateState",
    zValidator("json", UpdateStatePayload),
    handleInvoke("updateState", UpdateStatePayload, updateStateRequest)
  );

  app.post(
    "/:graphId/getSchema",
    zValidator("json", GetSchemaPayload),
    handleInvoke("getSchema", GetSchemaPayload, getSchemaRequest)
  );

  app.post(
    "/:graphId/getStateHistory",
    zValidator("json", GetStateHistoryPayload),
    handleStream(
      "getStateHistory",
      GetStateHistoryPayload,
      getStateHistoryRequest
    )
  );

  app.get("/ok", (c) => c.json({ ok: true }));

  app.onError((err, c) => {
    logger.error(err);
    if (err instanceof HTTPException && err.status === 401) {
      return err.getResponse();
    }

    const { message } = serializeError(err);
    return c.text(message || "Internal server error", 500);
  });

  serve({ fetch: app.fetch, hostname: "localhost", port: GRAPH_PORT }, (c) =>
    logger.info(`Listening to ${c.address}:${c.port}`)
  );
}

process.on("uncaughtExceptionMonitor", (error) => {
  logger.error(error);
  gracefulExit();
});

asyncExitHook(() => awaitAllCallbacks(), { wait: 3_000 });
main();
