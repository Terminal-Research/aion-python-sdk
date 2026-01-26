# Environment Variables Guide

Complete reference for all environment variables available in the Aion Agent SDK.

## Full Configuration Template

```bash
# Database Configuration
POSTGRES_URL=postgresql://your_username:your_password@localhost:5432/your_database_name

# Application Settings
LOG_LEVEL=INFO
AION_DOCS_URL=https://docs.aion.to/
LOGSTASH_HOST=0.0.0.0
LOGSTASH_PORT=5000

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
- The `.env` file is automatically loaded when running `poetry run aion serve`
- Undefined optional variables will use their default values or be set to `None`
- The system validates required variables on startup
