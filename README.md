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

The CLI provides four subcommands: `connect`, `ssh`, `exec`, and `copy`.

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


### `exec`: Execute a single command

Execute a single command on a target instance and return the results with proper exit codes.

```
uv run python -m session_manager_plugin.cli.main exec \
  --target i-0123456789abcdef0 \
  --command "ls -la /tmp" \
  --region us-west-2 \
  --profile myprofile
```

Options:
- `--target` (required): EC2 instance ID (`i-*`) or managed instance ID (`mi-*`)
- `--command` (required): Shell command to execute
- `--timeout`: Command timeout in seconds (default: 600)
- `--profile`, `--region`, `--endpoint-url`: AWS settings for `boto3`

This command:
- Executes the command and captures stdout/stderr separately
- Returns the actual exit code of the executed command
- Filters shell noise (prompts, command echoes) from output
- Useful for scripting and automation


### `copy`: File transfer via SSM

Transfer files to/from targets using base64 encoding over SSM sessions.

```
# Upload local file to remote
uv run python -m session_manager_plugin.cli.main copy \
  ./local-file.txt i-0123456789abcdef0:/tmp/remote-file.txt \
  --region us-west-2 \
  --profile myprofile

# Download remote file to local  
uv run python -m session_manager_plugin.cli.main copy \
  i-0123456789abcdef0:/tmp/remote-file.txt ./local-file.txt \
  --region us-west-2 \
  --profile myprofile
```

Options:
- `--verify-checksum` (default: on): Verify file integrity using MD5/SHA256
- `--encoding` (default: base64): Transfer encoding (base64, raw, uuencode)
- `--chunk-size`: Transfer chunk size in bytes (default: 8192)
- `--profile`, `--region`, `--endpoint-url`: AWS settings for `boto3`

Features:
- Automatic checksum verification to ensure file integrity  
- Progress reporting during transfer
- Support for binary files via base64 encoding
- Works with any file size (chunked transfer)


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

You can embed the plugin in your own Python program. There are four convenient levels: file transfer, exec API, high-level (CLI coordinator), and low-level (session + data channel).

### File Transfer API: Upload/download files

For programmatic file transfers with progress tracking and verification:

```python
from session_manager_plugin.file_transfer import FileTransferClient
from session_manager_plugin.file_transfer.types import FileTransferOptions

client = FileTransferClient()

# Upload file
options = FileTransferOptions(verify_checksum=True)
success = await client.upload_file(
    local_path="./local-file.txt",
    remote_path="/tmp/remote-file.txt", 
    target="i-0123456789abcdef0",
    options=options,
    region="us-west-2",
    profile="myprofile"
)

# Download file
success = await client.download_file(
    remote_path="/tmp/remote-file.txt",
    local_path="./downloaded-file.txt",
    target="i-0123456789abcdef0", 
    options=options,
    region="us-west-2",
    profile="myprofile"
)
```

### Exec API: Single command execution

For simple command execution with clean stdout/stderr separation:

```python
from session_manager_plugin.exec import run_command, run_command_sync

# Async version
result = await run_command(
    target="i-0123456789abcdef0",
    command="ls -la /tmp",
    region="us-west-2",
    profile="myprofile"
)
print(f"Exit code: {result.exit_code}")
print(f"Stdout: {result.stdout.decode('utf-8')}")
print(f"Stderr: {result.stderr.decode('utf-8')}")

# Sync version  
result = run_command_sync(
    target="i-0123456789abcdef0",
    command="sha256sum /path/to/file"
)
if result.exit_code == 0:
    checksum = result.stdout.decode('utf-8').strip().split()[0]
```

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


## Architecture

The codebase has been recently refactored for better maintainability and reliability:

- **Clean message handling**: AWS SSM protocol messages are parsed by dedicated `MessageParser` and routed to specialized handlers (`HandshakeHandler`, `StreamHandler`, `ControlHandler`)
- **Reliable file transfers**: Fixed line ending normalization issues that caused checksum mismatches
- **Modular design**: Session management, WebSocket communication, and CLI coordination are cleanly separated
- **Type safety**: Full mypy type checking with modern Python type hints

This provides a solid foundation for extending functionality while maintaining compatibility with the AWS SSM protocol.


## Known Limitations

- KMS encryption handshake is not implemented; sessions requiring encryption will not negotiate keys.
- Port/InteractiveCommands sessions are stubbed and not fully implemented.

