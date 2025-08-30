# Python Session Manager Plugin

The goal is to build a Python implementation of the [AWS Session Manager Plugin](https://github.com/aws/session-manager-plugin).

- Use uv for project management
- Use the current system python, which is 3.13.3
- The project will eventually be published as a pypi module that can be consumed in other projects
- Do _not_ use fake or mock data
- You MUST prioritize simple, readable code with minimal abstraction - avoid premature optimization. Strice for elegant, minimal solutions that reduce complexity. Focus on clear implementation that's easy to understand.
- The project must mirror the Go reference implementation as much as possible.

## Plan

- [] Research the existing Go implementation
    - Repository: https://github.com/aws/session-manager-plugin
    - `sessionmanagerplugin/session` contains the source code for core functionalities
    - `communicator/` contains the source code for websocket related operations
- [] Document the necessary phases of the Python implementation
- [] Test each phase after completion, don't move onto the next phase until I've verified
