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
- [ ] Phase 1 implementation
- [ ] Phase 2 implementation  
- [ ] Phase 3 implementation
- [ ] Phase 4 implementation
- [ ] Phase 5 implementation

## Development Workflow

1. Complete each phase fully before moving to the next
2. Test each phase thoroughly before proceeding
3. Update documentation if implementation differs from plan
4. Run linting and type checking: `uv run black src/ && uv run mypy src/`
5. Run tests: `uv run pytest`

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