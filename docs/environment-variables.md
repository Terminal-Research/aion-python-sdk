# Environment Variables Guide

Complete reference for all environment variables available in the Aion Agent SDK.

## Full Configuration Template

```bash
# Database Configuration
POSTGRES_URL=postgresql://your_username:your_password@localhost:5432/your_database_name
POSTGRES_POOL_MIN_SIZE=2
POSTGRES_POOL_MAX_SIZE=10
TASK_OWNERSHIP_REAPER=false

# Application Settings
LOG_LEVEL=INFO
AION_DOCS_URL=https://docs.aion.to/
LOGSTASH_HOST=0.0.0.0
LOGSTASH_PORT=5000
FILE_STORAGE_BACKEND=stub
ENCRYPTION_KEY=your_fernet_key_here
PUSH_NOTIFICATION_TIMEOUT_SECONDS=30

# AION API Client (Required)
AION_CLIENT_ID=your_client_id_here
AION_CLIENT_SECRET=your_client_secret_here
AION_API_HOST=https://api.aion.to
AION_API_KEEP_ALIVE=60
```

## Detailed Variable Reference

### Database Configuration

**`POSTGRES_URL`**
- Type: `string` (optional)
- PostgreSQL connection string in format: `postgresql://username:password@host:port/database`
- If not provided, the system automatically creates and uses in-memory storage when the agent starts
- Example: `postgresql://user:password@localhost:5432/aion_db`

**`TASK_OWNERSHIP_REAPER`**
- Type: `boolean` (optional, default `true`)
- Set to `0`, `false`, `no`, or `off` to disable
- Lets this process close out tasks whose execution lease expired, and tasks
  left active with no lease at all
- Only meaningful with `POSTGRES_URL` set: without a shared database there are
  no leases to reclaim
- Enable it only once every instance writing to that database renews its
  leases. A deployment where some instances still predate lease renewal would
  have their live work reclaimed as abandoned, so this is turned on in a later
  deployment than the one that introduces it

**`POSTGRES_POOL_MIN_SIZE`** / **`POSTGRES_POOL_MAX_SIZE`**
- Type: `integer` (optional)
- Defaults: `2` / `10`
- Two pools sit in front of one PostgreSQL: SQLAlchemy serves `tasks` and
  ADK, raw psycopg serves the LangGraph saver. Both are sized from these two
  values, so size the connection budget `pods x processes x pools` checked
  against the database's `max_connections`, not from the defaults
- For the SQLAlchemy pool, `POSTGRES_POOL_MIN_SIZE` becomes the base pool
  size and `POSTGRES_POOL_MAX_SIZE - POSTGRES_POOL_MIN_SIZE` becomes the
  allowed overflow above it
- Checkout timeout, queue depth, and pre-ping behavior are fixed internally
  and not exposed as separate variables

### Application Settings

**`LOG_LEVEL`**
- Type: `string`
- Default: `INFO`
- Controls logging verbosity
- Allowed values: `DEBUG`, `INFO`, `WARNING`, `ERROR`

**`AION_DOCS_URL`**
- Type: `string`
- Default: `https://docs.aion.to/`
- URL to the Aion API documentation

**`FILE_STORAGE_BACKEND`**
- Type: `string` (optional)
- Default: not set (disabled)
- Enables conversion of inline (base64) file parts in outgoing A2A events to URL references, minimizing binary content stored in task history tables
- When not set, file parts are passed through unchanged (base64 preserved)
- Allowed values: `stub`
  - `stub` — development/testing only; generates placeholder URLs without uploading any data

**`ENCRYPTION_KEY`**
- Type: `string` (optional)
- Default: not set (sensitive data is stored unencrypted)
- Encrypts sensitive data at rest. One key per deployment, shared by every subsystem that needs it — currently the push-notification configuration store, which persists callback URLs together with the credentials the receiving webhook expects
- Must be a URL-safe base64-encoded 32-byte Fernet key; a malformed value is rejected at startup
- Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- Applies to persistent storage only; when the agent falls back to in-memory storage nothing is written to disk
- Turning encryption **on** for an existing database is safe — rows already written in plaintext are still readable. **Changing** an existing key is not yet supported: data written under the old key becomes unreadable (see the `TODO(encryption)` in `push_notifications.py`)

**`PUSH_NOTIFICATION_TIMEOUT_SECONDS`**
- Type: `float` (optional)
- Default: `30.0`
- Read/write timeout for webhook deliveries. Raise it for a receiver that does real work on the callback before answering, which otherwise surfaces as `httpx.ReadTimeout` even though the request was accepted
- The connect timeout stays at `5.0` regardless: an unreachable host should fail fast
- The first delivery of a run is awaited in the request path, so this value also bounds how long a slow webhook can delay the `message/send` response

**`LOGSTASH_HOST`**
- Type: `string` (optional)
- Logstash server host for centralized logging
- Example: `0.0.0.0` or `logstash.example.com`

**`LOGSTASH_PORT`**
- Type: `integer` (optional)
- Logstash server port for centralized logging
- Example: `5000`

### AION API Client

**`AION_CLIENT_ID`**
- Type: `string` (required)
- Unique identifier for API authentication with Aion platform

**`AION_CLIENT_SECRET`**
- Type: `string` (required)
- Secret key for API authentication with Aion platform

**`AION_API_HOST`**
- Type: `string`
- Default: `https://api.aion.to`
- API host URL for Aion platform communication
- Must start with `http://` or `https://`

**`AION_API_KEEP_ALIVE`**
- Type: `integer`
- Default: `60`
- Keep alive interval in seconds for API connections

## Usage Notes

- Variables are case-insensitive
- The `.env` file is automatically loaded when running `aion serve`
- Undefined optional variables will use their default values or be set to `None`
- The system validates required variables on startup
