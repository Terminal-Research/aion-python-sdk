# Extension exposure

Which A2A extensions this server knows, which it runs, and which it tells the
world about are three separate questions. They are answered in three separate
places, and conflating any two of them is the mistake this page exists to
prevent.

The registry is `AionA2AExtensionRegistry`
(`src/aion/core/runtime/context/extensions/registry.py`); one
`ExtensionDescriptor`
(`src/aion/core/runtime/context/extensions/descriptors.py`) per extension is
the whole record.

## The four states

**Known / supported.** A descriptor is registered, so the server recognizes
the URI: a request declaring it is collected, its payload validated by the
descriptor's collector, and the result exposed on the runtime context. A URI
with no descriptor is carried as an inert `UnknownExtension` — visible for
logging and forwarding, never verified and never routed.

**Active.** The extension is enabled for the agent now running. Most
protocol-level extensions register `active=True`; agent-specific features
register `active=False` and are switched on by `enabled_extensions` in the
agent's `aion.yaml`, through `activate()`. A request declaring an inactive
extension is rejected, and the message names `enabled_extensions` as the fix.

**Advertised.** The extension may be published on the generated `AgentCard`.
`AionAgentCard.from_config` reads `get_advertised()` and nothing else.
`get_advertised()` returns the descriptors that are marked advertised and
whose deployment prerequisites are satisfied: active, available, and with
every extension in their `requires` chain active and available too. What the
deployment cannot run is withheld, because a card that offers a call this
server could never answer is worse than one that stays quiet about it. That
is a floor rather than a guarantee — an advertised extension has what the
deployment owes it, and a request declaring it can still be refused by
`_verify()` for a per-request co-activation the card cannot know about. The
evolution extension is the case that makes the prerequisite check matter:
`activate()` is additive and expands nothing, so an agent that lists
evolution in `enabled_extensions` without the daemon extension it requires
has an extension that is active and refused on every request.

**Unavailable.** The extension is enabled but cannot function on this
deployment — most often an optional toolkit that is not installed. The
component that owns those runtime dependencies records the reason with
`mark_unavailable()`, the request-time verifier rejects the extension with
exactly that reason, and the card omits it.

`active` and `advertised` are independent in both directions, and both
directions occur in the shipped registry:

| Extension | `active` | `advertised` | On a standard card |
| --- | --- | --- | --- |
| Distribution, messaging, cards, event, traceability, usage attribution | `True` | `True` | yes |
| `GetContext`, `GetContexts` | `True` | `False` | no — supported, not announced |
| Daemon, behaviour evolution | `False` | `True` | only once the agent enables it |

## Extension points and transport bindings

*Extension* is the umbrella term. The A2A specification distinguishes several
kinds beneath it, and two of them matter here:

- A **message extension** augments or alters the data of a standard request —
  a payload in `params.metadata`, typed parts on a message, a header. Most of
  the registry is this kind.
- A **method extension** adds an RPC method of its own. It is no less an
  extension for it: it has a URI, a descriptor, and the same activation and
  exposure rules as any other. `GetContext` and `GetContexts` are method
  extensions.

An extension may use more than one of these at once, which is why there is no
`kind` field on the descriptor to disagree with reality. What an extension
actually does is read off the mechanisms it is wired into: a **collector**
handles its data, a **method binding** gives it an RPC method, an
**`ExtensionTaskHandler`** gives it ownership of a task's execution.

Identity is shared; transport is not. A method extension's routing belongs to
the binding layer of whichever transport carries it —
`AION_JSONRPC_METHOD_EXTENSION_BINDINGS`
(`src/aion/core/a2a/method_extensions.py`) for JSON-RPC, read by
`AionJsonRpcDispatcher`. A binding holds a method name, a params model, the
handler that answers it, and the URI of the extension that defines it:

```
extension registry  ->  identity, active, available, advertised
JSON-RPC binding    ->  method name, params model, handler, extension URI
task handler        ->  ownership of task execution
```

That table *is* the dispatcher's routing: it resolves the params model and the
handler from the binding and names no method of its own, so a binding cannot
describe one route while the code takes another.

It deliberately has no `advertised`, `internal` or `active` field. A binding
that repeated the descriptor's exposure could contradict it; having nothing to
contradict it with, it cannot.

The link to the descriptor is then enforced twice, and the two are different
claims on different clocks:

- **Startup wiring validation.** `AionJsonRpcDispatcher` refuses to be
  constructed when a binding names a URI no descriptor claims, or a handler
  the request handler does not have. These are facts about the code: no
  request can make a missing descriptor appear, and a method bound to a URI
  nothing registers would answer calls with an exposure policy governing
  nothing. Settled once, at startup.
- **Request-time state enforcement.** On every call, the dispatcher asks the
  registry whether the extension behind the method is ready on this
  deployment — `unservable_reason()`: registered, enabled for this agent,
  available here, and with everything in its `requires` chain active and
  available too. A deployment can change any of these while the process
  runs, so this cannot be a startup check.

That second check is *deployment-level readiness*, and it is deliberately not
everything `_verify()` asks. An extension declared on a message is held to
readiness **and** to per-request activation: the extensions it requires must
be declared on that same request, not merely be active on the agent. A method
extension is invoked directly and sends no `A2A-Extensions` declaration at
all, so there is no co-activation to check and readiness is the whole
question. The consequence is worth stating plainly: a URI the registry calls
serviceable can still be refused by `_verify()`, on a request that declared it
without its requirements.

`advertised` is in neither check. Whether the extension is announced on the
Agent Card decides what a client is told, never what it may call.
The extension URI also travels on the call context of every request the
dispatcher routes, beside the transport-level method name.

**The extension point does not decide public exposure.** A method extension
registered with `advertised=True` is published on the Agent Card like any
other extension. `GetContext` and `GetContexts` are withheld because of the
exposure policy below, not because of what kind of extension they are. What
puts an extension on a card is `advertised`, activation, availability and its
`requires` — in one place, `get_advertised()`.

## Task-routing: `ExtensionTaskHandler`

An extension either owns the execution of a task or it does not, and the
answer is structural rather than declared. An **execution-owning** (or
**task-routing**) extension is one with an `ExtensionTaskHandler`
(`src/aion/server/agent/execution/extensions/base.py`): it takes `stream()`,
`resume()` and `cancel()` from the agent's framework adapter and drives its
own toolkit instead. Behaviour evolution is the one such extension today.
Every other registered extension contributes metadata or payloads to a request
that the framework adapter still executes.

`ExtensionTaskHandler` is therefore the single source of truth about
task-execution ownership. Do not add a `functional`, `has_behavior` or similar
flag to the descriptor: it would be imprecise about what it claims and
duplicate something the handler already answers exactly — and the two could
then disagree.

## Internal and non-advertised is not deprecated

`GetContext` and `GetContexts` are **current internal, non-advertised Aion
A2A method extensions**. They are supported, enabled, callable, and their
URIs are stable identifiers that are not going to be recycled. What
`advertised=False` says is narrower than it may look: a standard Aion agent
does not present them among its capabilities, because they are read handlers
and not an implementation of the platform's unified Context lifecycle. This
server declares neither that lifecycle, nor context summaries, nor
`DeleteContext`.

A custom implementation that genuinely fulfils a broader contract may register
either URI itself with `advertised=True`; `register()` replaces by URI, and
that implementation is then responsible for the contract it announces.

Their canonical URIs are also their specification pages, which is why the URIs
are not free to change:

| Method | Canonical URI / specification |
| --- | --- |
| `GetContext` | <https://docs.aion.to/a2a/extensions/aion/context/get-context/1.0.0> |
| `GetContexts` | <https://docs.aion.to/a2a/extensions/aion/context/get-contexts/1.0.0> |

`GET_CONTEXT_EXTENSION_URI_V1` and `GET_CONTEXTS_LIST_EXTENSION_URI_V1`
(`src/aion/core/constants/a2a.py`) are those two strings. The wire models each
page specifies — `Conversation`, `ConversationTaskStatus`, `ContextsList` —
are typed in `src/aion/core/a2a/`, and the JSON-RPC methods that carry them in
`src/aion/core/a2a/method_extensions.py`. Both pages are published outside the
Agent Extensions navigation: reachable by their URI, which identifiers need,
without being presented as a capability of a standard agent.

## It is not an authorization mechanism

Withholding an extension from the card changes what a client is *told*, never
what a client is *allowed*. A non-advertised extension is invoked exactly like
an advertised one, under the same authentication and caller-scoping rules —
context reads resolve history through the same effective caller scope used
when tasks are saved, and an anonymous caller receives an empty projection
rather than shared history. Anything that must actually be refused is refused
at request time - by activation state, availability, the extension's
requirements, or the caller's scope.
