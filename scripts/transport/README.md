# Explicit transport diagnostics

These standalone tools were retained from the local experiment checkout. They are not automatically enabled by the controller; the default gateway remains `manipulation_agent.provider_gateway`.

- `trace_responses_transport.py`: HTTP request/response tracing with payload hashes and tool schemas, plus opt-in `strict_mcp_tools_v1` schema enforcement. Credentials and input/image bodies are excluded from trace records. Schema mode changes must be recorded in the experiment protocol.
- `responses_compat_proxy.py`: compatibility handling for the historical Responses provider. Review its `--help` and freeze its source when using it.

Use the current controller's explicit provider-gateway option to select an endpoint. The existing processes using the old checkout retain that frozen copy; moving these files does not redirect a running experiment. HTTP fixtures test header/payload preservation and trace redaction; they are not a live-provider acceptance result.
