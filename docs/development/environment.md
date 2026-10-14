# Development Environment

## Python Version

**Python 3.13+** is recommended for development. In Python 3.12, the debugger hooks into every bytecode frame including dynamic imports, which significantly slows down plugin registration in debug mode — sometimes by up to 10x. Python 3.13 resolves this with a more targeted monitoring system.

## Environment Variables

Create a `.env` file in your project root. For a full reference of all available variables, see the
**[Environment Variables Guide](https://docs.aion.to/sdk/python/configuration/environment-variables)**.

The `aion` CLI loads that file from the directory it runs in, before any command starts; a variable
already set in the environment keeps its value. Nothing else in the SDK reads it: settings come from
the process environment only. The test suites never see it either - see
[The environment a test runs in](../../tests/README.md#the-environment-a-test-runs-in).
