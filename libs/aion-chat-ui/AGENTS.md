# Aion Chat UI Agent Notes

This package contains the standalone React/Ink terminal chat UI for Aion Chat.
It is packaged into the Python SDK through `npm run stage:python`, which copies
the built CLI bundle into `libs/aion-sdk/src/aion/cli/bin/cli.mjs`.

## Inspecting Aion Chat Session Logs

Aion Chat writes operational session logs as JSONL files in the user's config
directory, not in the project directory. These logs are useful when an agent
needs to understand what a previously run Aion Chat session sent, received, or
rendered.

Default log directory:

```sh
~/.config/aion/chat-session-logs/
```

If `XDG_CONFIG_HOME` is set, the directory may instead be:

```sh
$XDG_CONFIG_HOME/aion/chat-session-logs/
```

To list the newest logs:

```sh
ls -t ~/.config/aion/chat-session-logs | head
```

To inspect the most recent log:

```sh
LOG="$(ls -t ~/.config/aion/chat-session-logs/*.jsonl | head -1)"
tail -n 200 "$LOG"
```

Each line is one JSON object. Useful events for reconstructing agent behavior
include:

- `chat.agent_message.rendered`
- `chat.agent_message.stream_replaced`
- `chat.stream_delta.prepared`
- `a2a.stream.event`
- `a2a.response.received`
- `a2a.request.failed`

To search for agent output and stream activity:

```sh
rg "chat.agent_message|a2a.stream.event|a2a.response.received|chat.stream_delta.prepared" "$LOG"
```

If `jq` is available, this filters the most useful entries:

```sh
jq 'select(.event == "a2a.stream.event" or .event == "chat.agent_message.rendered" or .event == "chat.agent_message.stream_replaced")' "$LOG"
```

The active log path is also recorded near startup in the
`chat.session.started` event as `data.logFilePath`.
