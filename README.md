# Python Session Manager Plugin

A Python implementation of the AWS Session Manager Plugin. It speaks the same binary protocol as the official Go plugin and supports interactive shell sessions over SSM.

Highlights:
- Interactive SSH-like sessions via SSM (`ssh` subcommand)
- Direct connect to an existing SSM data channel (`connect` subcommand)
- Proper ack/seq handling, terminal raw mode, signal forwarding (Ctrl-C/Z/\), and periodic resize updates
- Minimal logging by default; verbose traces with `-v`


## Requirements

- Python 3.13+
- AWS credentials (for `ssh`) discoverable via environment or `~/.aws/credentials`

Install (editable):

```
uv pip install -e .
```

Run with `uv`:

```
uv run python -m session_manager_plugin.cli.main --help
```


## CLI Usage

The CLI provides two subcommands: `connect` and `ssh`.

### `ssh`: Start an SSM session to a target

Starts a new SSM session using `boto3` and connects interactively.

```
uv run python -m session_manager_plugin.cli.main ssh \
  --target i-0123456789abcdef0 \
  --region us-west-2 \
  --profile myprofile
```

Options:
- `--target` (required): EC2 instance ID (`i-*`) or managed instance ID (`mi-*`)
- `--document-name`: SSM document (defaults to the agent’s standard shell doc)
- `--parameters`: JSON object of document parameters
- `--profile`, `--region`, `--endpoint-url`: AWS settings for `boto3`

Global options (apply to all commands):
- `-v/--verbose`: enable DEBUG logging
- `--log-file PATH`: log to file in addition to stderr
- `--coalesce-input [auto|on|off]` (default `auto`): input batching
  - `auto`: enabled for non‑TTY stdin (piped input), disabled for interactive TTYs
  - `on`: always enable (tunable with `--coalesce-delay-ms`)
  - `off`: always disabled
- `--coalesce-delay-ms FLOAT` (default 10.0): coalescing delay when enabled

Behavior:
- Terminal set to cbreak, `-echo -isig` (no double echo; signals forwarded to remote)
- Periodic terminal size updates (500ms) and on resize (SIGWINCH)
- Ctrl-C (0x03), Ctrl-\ (0x1c), Ctrl-Z (0x1a) forwarded to remote
- `exit` cleanly closes the session and the CLI


### `connect`: Attach to an existing SSM data channel

Connects using session parameters you already have (typical when called by AWS CLI). You can pass a single JSON blob or individual flags.

JSON form (mimics the AWS CLI invocation):

```
uv run python -m session_manager_plugin.cli.main connect '{
  "SessionId": "dacort-abc123",
  "StreamUrl": "wss://ssmmessages.us-west-2.amazonaws.com/v1/data-channel/dacort-abc123?...",
  "TokenValue": "...",
  "target": "i-0123456789abcdef0",
  "sessionType": "Standard_Stream"
}'
```

Flag form:

```
uv run python -m session_manager_plugin.cli.main connect \
  --session-id dacort-abc123 \
  --stream-url wss://... \
  --token-value ... \
  --target i-0123456789abcdef0 \
  --session-type Standard_Stream
```

Notes:
- The CLI validates parameters and will emit friendly errors if missing or invalid.
- Global logging and coalescing options work here too.


## Using as a Library

You can embed the plugin in your own Python program. There are two convenient levels: high-level (CLI coordinator) and low-level (session + data channel).

### High-level: reuse the CLI coordinator

```
import asyncio
from session_manager_plugin.cli.types import ConnectArguments
from session_manager_plugin.cli.main import SessionManagerPlugin
from session_manager_plugin.utils.logging import setup_logging

setup_logging()  # or logging.DEBUG for verbose

args = ConnectArguments(
    session_id="dacort-abc123",
    stream_url="wss://...",
    token_value="...",
    target="i-0123456789abcdef0",
    session_type="Standard_Stream",
)

plugin = SessionManagerPlugin()
exit_code = asyncio.run(plugin.run_session(args))
```

### Low-level: wire session + data channel yourself

```
import asyncio
from session_manager_plugin.session.session_handler import SessionHandler
from session_manager_plugin.communicator.utils import create_websocket_config
from session_manager_plugin.communicator.data_channel import SessionDataChannel

async def main():
    handler = SessionHandler()
    # Create session (without executing) from your already-resolved params
    session = await handler.validate_input_and_create_session({
        "sessionId": "dacort-abc123",
        "streamUrl": "wss://...",
        "tokenValue": "...",
        "target": "i-0123456789abcdef0",
        "sessionType": "Standard_Stream",
    })

    # Create data channel and wire handlers
    ws_cfg = create_websocket_config(stream_url="wss://...", token="...")
    dc = SessionDataChannel(ws_cfg)
    dc.set_input_handler(lambda b: sys.stdout.buffer.write(b) or sys.stdout.flush())
    # Optional: closed and coalescing handlers
    dc.set_closed_handler(lambda: print("\n[session closed]\n"))
    # dc.set_coalescing(True, delay_sec=0.01)  # for piped input

    session.set_data_channel(dc)
    await session.execute()        # open websocket + handshake
    # then run your own event loop + stdin handling as needed

asyncio.run(main())
```

Tips:
- For custom UIs, provide your own `input_handler` (bytes from the agent) and feed `send_input_data()` with bytes from your UI.
- Use `send_terminal_size(cols, rows)` whenever your layout changes.
- On shutdown, call `await session.terminate_session()`.


## Development

Run tests:

```
uv run pytest -q
```

Formatting & linting:

```
uv run black .
uv run ruff check .
uv run mypy .
```

Logging:
- By default, logs are minimal. Use `-v` or set up `setup_logging(level=logging.DEBUG)` programmatically for detailed traces.


## Known Limitations

- KMS encryption handshake is not implemented; sessions requiring encryption will not negotiate keys.
- Port/InteractiveCommands sessions are stubbed and not fully implemented.

