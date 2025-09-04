# Aion YAML Configuration Guide

## Table of Contents

- [Overview](#overview)
- [File Structure](#file-structure)
- [Agent Configuration](#agent-configuration)
  - [Basic Syntax](#basic-syntax)
  - [Simple Agent Configuration](#simple-agent-configuration)
  - [Path Format Options](#path-format-options)
  - [Complex Agent Configuration](#complex-agent-configuration)
  - [Configuration Properties](#configuration-properties)
- [Configuration Field Types](#configuration-field-types)
  - [String Fields](#string-fields)
  - [Numeric Fields](#numeric-fields)
  - [Boolean Fields](#boolean-fields)
  - [Array Fields](#array-fields)
  - [Object Fields](#object-fields)
  - [Common Properties](#common-properties)
- [Dependencies Configuration](#dependencies-configuration)
- [Environment Variables Configuration](#environment-variables-configuration)
- [MCP Proxy Configuration](#mcp-proxy-configuration)
- [HTTP Services Configuration](#http-services-configuration)

---

## Overview

The `aion.yaml` file is the main configuration file for your Aion project. It defines agents, dependencies, services, and deployment settings.

## File Structure

```yaml
aion:
  agent:
    # Agent configurations (see "Agent Configuration" section below)
  dependencies:
    # Package dependencies (see "Dependencies Configuration" section below)
  env:
    # Environment variables (see "Environment Variables Configuration" section below)
  mcp:
    # MCP proxy settings (see "MCP Proxy Configuration" section below)
  http:
    # Web services (see "HTTP Services Configuration" section below)
```

---

## Agent Configuration

The `agent` section defines AI agents in your Aion project. Each agent can be configured with a simple string path or a detailed configuration object.

### Basic Syntax

```yaml
aion:
  agent:
    agent_id: "path/to/agent.py"
    # OR
    agent_id:
      path: "path/to/agent.py"
      # additional configuration...
```

### Simple Agent Configuration

For basic agents, you can use just a string path:

```yaml
aion:
  agent:
    # Graph instance variable (StateGraph)
    chat_bot: "./src/agents/chat.py:chat_graph"
    
    # Function that creates and returns a graph
    analyzer: "./src/agents/analysis.py:create_analyzer"
    
    # BaseAgent child class
    assistant: "./src/agents/assistant.py:AssistantAgent"
    
    # File with auto-discovery
    support: "./src/agents/support.py"
```

### Path Format Options

| Format | Description | Example |
|--------|-------------|---------|
| **Graph instance** | Points to a `StateGraph` variable | `"./path/to/file.py:variable_name"` |
| **Graph factory function** | Points to function returning a graph | `"./path/to/file.py:function_name"` |
| **BaseAgent class** | Points to `BaseAgent` subclass | `"./path/to/file.py:ClassName"` |
| **Auto-discovery** | Automatically finds graph/agent in file | `"./path/to/file.py"` |

### Complex Agent Configuration

For advanced agents with specific capabilities and settings:

```yaml
aion:
  agent:
    advanced_assistant:
      # Required: Path to agent implementation
      path: "./src/agents/advanced.py:advanced_graph"
      
      # Basic Information
      name: "Advanced AI Assistant"
      description: "A sophisticated AI agent with multiple capabilities including data analysis, web search, and document processing"
      version: "2.1.0"
      
      # Input/Output Capabilities
      input_modes: ["text", "audio", "image", "json"]
      output_modes: ["text", "audio", "json"]
      
      # System Capabilities
      capabilities:
        streaming: true
        pushNotifications: true
      
      # Agent Skills
      skills:
        - id: "web_search"
          name: "Web Search"
          description: "Search the internet for current information"
          tags: ["search", "web", "research"]
          examples:
            - "Search for latest news about AI"
            - "Find information about Python libraries"
            
        - id: "data_analysis" 
          name: "Data Analysis"
          description: "Analyze CSV, JSON, and Excel files"
          tags: ["data", "analysis", "statistics"]
          examples:
            - "Analyze sales data from CSV file"
            - "Generate charts from dataset"
            
        - id: "document_processing"
          name: "Document Processing" 
          description: "Process and extract information from documents"
          tags: ["documents", "pdf", "text"]
          examples:
            - "Summarize PDF document"
            - "Extract key information from reports"
      
      # Custom Configuration
      configuration:
        # String Configuration
        api_key:
          type: "string"
          description: "API key for external services"
          required: true
          min_length: 10
          max_length: 100
        
        # Integer Configuration
        max_retries:
          type: "integer"
          description: "Maximum number of retry attempts"
          default: 3
          min: 0
          max: 10

        # Float Configuration
        temperature:
          type: "float"
          description: "Model creativity"
          default: 0.7
          min: 0.0
          max: 2.0
          
        # Boolean Configuration
        enable_streaming:
          type: "boolean"
          description: "Enable streaming responses"
          default: true
        
        # Array Configuration
        supported_languages:
          type: "array"
          description: "Supported languages"
          min_length: 1
          max_length: 20
          items:
            type: "string"
            enum: ["en", "es", "fr", "de", "ru", "uk", "zh", "ja"]
          default: ["en"]
        
        # Object Configuration
        model_config:
          type: "object"
          description: "AI model configuration"
          required: true
          items:
            model_name:
              type: "string"
              description: "Name of the AI model to use"
              enum: ["gpt-4", "gpt-3.5-turbo", "claude-3", "local-model"]
              default: "gpt-4"
            max_tokens:
              type: "integer"
              description: "Maximum response tokens"
              default: 4000
              min: 100
              max: 8000
            use_cache:
              type: "boolean"
              description: "Enable model response caching"
              default: true
```

### Configuration Properties

#### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `path` | string | Path to agent implementation |

#### Optional Metadata

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | `"Agent"` | Human-readable agent name |
| `description` | string | `""` | Detailed agent description |
| `version` | string | `"1.0.0"` | Semantic version (X.Y.Z format) |

#### Input/Output Modes

| Field | Type | Default | Valid Values |
|-------|------|---------|--------------|
| `input_modes` | array | `["text"]` | `"text"`, `"audio"`, `"image"`, `"video"`, `"json"` |
| `output_modes` | array | `["text"]` | `"text"`, `"audio"`, `"image"`, `"video"`, `"json"` |

#### Capabilities

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `streaming` | boolean | `false` | Support streaming responses |
| `pushNotifications` | boolean | `false` | Support push notifications |

#### Skills Array

Each skill object contains:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | ✓ | Unique skill identifier |
| `name` | string | ✓ | Human-readable skill name |
| `description` | string | | Detailed skill description |
| `tags` | array | | Categorization tags |
| `examples` | array | | Usage examples |

---

## Configuration Field Types

Agent configuration supports multiple field types with comprehensive validation options.

### String Fields

```yaml
field_name:
  type: "string"
  description: "Field description"
  default: "default_value"
  required: true           # Field is mandatory
  nullable: false          # Cannot be null
  min_length: 1           # Minimum character count
  max_length: 255         # Maximum character count
  enum: ["option1", "option2", "option3"]  # Allowed values only
```

**String validation options:**
- `required`: Must be provided in configuration
- `nullable`: Can accept `null` as valid value
- `min_length`/`max_length`: Character count limits
- `enum`: Restricts to specific values
- `default`: Used when field not provided

### Numeric Fields

```yaml
# Integer
count:
  type: "integer" 
  description: "Item count"
  default: 10
  min: 1                  # Minimum value
  max: 1000              # Maximum value
  required: false
  nullable: true         # Can be null
  enum: [10, 50, 100]    # Specific allowed values

# Float  
ratio:
  type: "float"
  description: "Ratio value"
  default: 0.5
  min: 0.0               # Minimum value (inclusive)
  max: 1.0               # Maximum value (inclusive)
```

**Numeric validation:**
- `min`/`max`: Value range limits (inclusive)
- `enum`: Restricts to specific numeric values
- `nullable`: Allows `null` for optional numeric fields
- Integers must be whole numbers, floats allow decimals

### Boolean Fields

```yaml
enabled:
  type: "boolean"
  description: "Enable this feature"
  default: true
  required: false        # Optional field
  nullable: true         # Can be null (tri-state: true/false/null)
```

**Boolean validation:**
- Accepts: `true`, `false`
- With `nullable: true`: also accepts `null`
- `default`: Provides fallback value when not specified
- Useful for feature flags and toggle switches

### Array Fields

#### Simple Array (primitives)

```yaml
# Array of strings
tags:
  type: "array"
  description: "List of tags"
  min_length: 1
  max_length: 10
  items:
    type: "string"
    enum: ["tag1", "tag2", "tag3"]
  default: ["tag1"]

# Array of numbers
scores:
  type: "array"
  description: "List of scores"
  items:
    type: "integer"
    min: 0
    max: 100
  default: [85, 92]
```

#### Complex Array (objects)

```yaml
endpoints:
  type: "array"
  description: "API endpoints configuration"
  min_length: 1
  items:
    type: "object"
    items:
      url:
        type: "string"
        required: true
      timeout:
        type: "integer"
        default: 30
      enabled:
        type: "boolean"
        default: true
```

**Array validation:**
- `min_length`/`max_length`: Number of items in array
- `items`: Schema for array elements (optional)
- Simple arrays contain primitives (string, number, boolean)
- Complex arrays contain objects with their own validation
- Each array item follows the defined schema

### Object Fields

```yaml
settings:
  type: "object"
  description: "Nested configuration"
  required: true
  nullable: false
  items:
    property1:
      type: "string"
      default: "value"
    property2:
      type: "integer" 
      default: 42
    nested_config:
      type: "object"
      items:
        sub_property:
          type: "boolean"
          default: true
```

**Object validation:**
- `required`: Object must be provided
- `nullable`: Object can be `null`
- `items`: Defines properties within the object
- Objects can be nested to any depth
- Each property has its own type and validation rules

### Common Properties

All field types support these properties:

| Property | Type | Description |
|----------|------|-------------|
| `type` | string | Field type (required) |
| `description` | string | Human-readable description |
| `default` | any | Default value when not provided |
| `required` | boolean | Whether field must be specified |
| `nullable` | boolean | Whether field can be `null` |

#### Type-Specific Properties

**String & Array Length**
- `min_length`: Minimum length/count
- `max_length`: Maximum length/count

**Numeric Range**
- `min`: Minimum value (inclusive)
- `max`: Maximum value (inclusive)

**Value Restrictions**
- `enum`: Array of allowed values (any type)

**Nested Structure**
- `items`: Schema for array elements or object properties

---

## Dependencies Configuration

The `dependencies` section defines Python packages and local modules required for your Aion project.

### Syntax

```yaml
aion:
  dependencies:
    src: "./src"
```

### Rules

- Point to directory containing `pyproject.toml`, not the file itself
- Use relative paths starting with `"./"`
- Use `"."` for current directory packages

---

## Environment Variables Configuration

The `env` section specifies environment variables for your agents and services. This section is optional.

### Syntax

```yaml
aion:
  env: "./.env"
```

### Configuration

Path to `.env` file containing environment variables:

```bash
# .env file example
POSTGRES_URL=postgresql://localhost:5432/aion # optional
```

### Priority

Values in `aion.yaml` override Aion Cloud deployment environment variables.

---

## MCP Proxy Configuration

The `mcp` section configures Model Context Protocol proxy for external service integration. This section is optional.

### Syntax

```yaml
aion:
  mcp:
    port: 9000
```

### Configuration

**Port**: Port number where local MCP instance is running

```yaml
mcp:
  port: 9000
```

### How It Works

- Mounts MCP proxy at `/mcp` endpoint
- Forwards requests to `localhost:{port}`
- Allows agents to communicate with MCP services through the proxy

### Requirements

- Local MCP service must be running on specified port
- Port must be available and not used by other services

---

## HTTP Services Configuration

The `http` section mounts Starlette/FastAPI applications that will be dynamically loaded and served. This section is optional.

### Syntax

```yaml
aion:
  http:
    api: "./src/agent/webapp.py:app"
```

### Path Format

`"./path/to/file.py:app_variable"`

- **File path**: Relative path to Python file
- **App variable**: Name of FastAPI/Starlette application after `:`
