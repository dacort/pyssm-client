# Claude Development Notes

## Project Overview

This is a Python implementation of the AWS Session Manager Plugin, porting functionality from the Go reference implementation at https://github.com/aws/session-manager-plugin.

## Key Requirements

- Use uv for project management with Python 3.13.3
- Mirror the Go reference implementation as closely as possible
- Prioritize simple, readable code with minimal abstraction
- No fake or mock data in production code
- Eventually publish as PyPI module

## Phase-by-Phase Implementation

The implementation is divided into 5 phases with detailed documentation in `docs/`:

1. **Phase 1**: `docs/00_project_setup.md` - Project setup and foundation
2. **Phase 2**: `docs/01_session_management.md` - Core session management
3. **Phase 3**: `docs/02_websocket_communication.md` - WebSocket communication layer
4. **Phase 4**: `docs/03_integration_cli.md` - CLI integration and coordination
5. **Phase 5**: `docs/04_testing_packaging.md` - Testing and PyPI packaging

## Current Status

- [x] Documentation created for all phases
- [x] **Phase 1 implementation** ✅ - Project setup and foundation complete
- [x] **Phase 2 implementation** ✅ - Core session management complete
- [x] **Phase 3 implementation** ✅ - WebSocket communication complete
- [x] **Phase 4A implementation** ✅ - Core CLI structure and connect command complete
- [ ] Phase 4B implementation - SSH command and AWS SSM integration
- [ ] Phase 5 implementation

### Phase 1 Completed ✅
- uv project initialized with Python 3.13.3
- Package structure created with src/ layout
- Dependencies configured (websockets, boto3, click, pydantic)
- Development tools setup (black, mypy, ruff, pytest)
- CLI entry point working

### Phase 2 Completed ✅
- Session types and enums implemented with dataclasses
- Protocol interfaces using typing.Protocol for clean duck typing
- Core Session class with full lifecycle management
- Plugin registry with singleton pattern and thread safety
- SessionHandler with comprehensive validation and error handling
- Three default plugins: StandardStream, Port, InteractiveCommands
- 20/20 unit tests passing, 100% mypy type checking success
- Idiomatic Python patterns throughout (async/await, match/case, etc.)

### Phase 3 Completed ✅
- WebSocket communication types and enums
- Core WebSocketChannel with connection lifecycle management
- SessionDataChannel implementing IDataChannel protocol
- Ping/pong health check mechanism with proper timeout handling
- WebSocket utilities for URL building and validation
- Upgraded to websockets 15.0.1 with new asyncio implementation
- 49/49 WebSocket component tests passing
- Exception-based connection state checking (modern websockets pattern)

### Phase 4A Completed ✅
- Click-based CLI with subcommand structure (`session-manager-plugin connect`)
- ConnectArguments dataclass with JSON parsing and validation
- SessionManagerPlugin coordinator integrating all Phase 1-3 components
- Signal handling for graceful shutdown (SIGINT/SIGTERM)
- stdin/stdout handling for interactive sessions
- Enhanced logging with file output support
- Integration testing framework with 12 comprehensive tests
- Data channel timing bug fixed (session creation before execution)
- AWS JSON case compatibility (PascalCase: SessionId, StreamUrl, TokenValue)
- Successfully tested with real AWS SSM session parameters
- 81/81 total tests passing, full type checking and linting compliance

## Development Workflow

1. Complete each phase fully before moving to the next
2. Test each phase thoroughly before proceeding
3. Update documentation if implementation differs from plan
4. Run linting and type checking: `uv run black src/ && uv run mypy src/`
5. Run tests: `uv run pytest`
6. Test CLI connect command: `uv run python -m session_manager_plugin.cli.main --verbose connect '{"SessionId": "...", "StreamUrl": "wss://...", "TokenValue": "...", "target": "..."}'`

## Key Dependencies

- `websockets` - WebSocket client implementation
- `click` - CLI interface framework (noted in Phase 4 docs)
- `boto3` - AWS SDK integration
- `pydantic` - Data validation and serialization

## Important Notes

- The CLI uses Click framework for argument parsing and command structure
- WebSocket communication must handle both text and binary message types
- Session management requires a plugin registry system for different session types
- All async operations should use proper asyncio patterns
- Error handling must be comprehensive for production reliability

## Testing Requirements

- Unit tests for each component
- Integration tests for component interaction
- End-to-end tests with AWS (using mock endpoints where appropriate)
- Performance tests for concurrent operations

## Post-Implementation Updates

After completing each phase, update the corresponding documentation in `docs/` if:
- Implementation approach changed from the original plan
- Additional dependencies were required
- Testing revealed issues requiring design changes
- Performance optimizations were needed

## Reference Implementation

Go source code structure to mirror:
- `sessionmanagerplugin/session/` → `src/session_manager_plugin/session/`
- `communicator/` → `src/session_manager_plugin/communicator/`
- Main entry point → `src/session_manager_plugin/cli.py`