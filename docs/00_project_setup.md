# Phase 1: Project Setup & Foundation

## Overview

This phase establishes the foundation for the Python Session Manager Plugin project using uv for project management and Python 3.13.3.

## Objectives

- Initialize uv project with proper configuration
- Create package structure mirroring the Go implementation
- Set up development dependencies and tooling
- Establish initial module architecture

## Implementation Steps

### 1. Initialize uv Project

```bash
# Initialize new uv project
uv init python-session-manager-plugin

# Verify Python 3.13.3 is being used
uv python pin 3.13.3
```

### 2. Configure pyproject.toml

```toml
[project]
name = "python-session-manager-plugin"
version = "0.1.0"
description = "Python implementation of AWS Session Manager Plugin"
authors = [
    {name = "Your Name", email = "your.email@example.com"},
]
dependencies = [
    "websockets>=12.0",
    "boto3>=1.34.0",
    "click>=8.1.0",
    "pydantic>=2.5.0",
]
requires-python = ">=3.13"
readme = "README.md"
license = {text = "Apache-2.0"}

[project.scripts]
session-manager-plugin = "session_manager_plugin.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
dev-dependencies = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "black>=23.0.0",
    "mypy>=1.8.0",
    "ruff>=0.1.0",
    "coverage>=7.0.0",
]
```

### 3. Create Package Structure

```
python-session-manager-plugin/
├── src/
│   └── session_manager_plugin/
│       ├── __init__.py
│       ├── session/
│       │   ├── __init__.py
│       │   ├── session.py
│       │   ├── session_handler.py
│       │   └── session_types.py
│       ├── communicator/
│       │   ├── __init__.py
│       │   └── websocket_channel.py
│       ├── utils/
│       │   ├── __init__.py
│       │   └── logging.py
│       └── cli.py
├── tests/
│   ├── __init__.py
│   ├── test_session/
│   ├── test_communicator/
│   └── test_utils/
├── docs/
├── pyproject.toml
└── README.md
```

### 4. Create Initial Module Files

#### src/session_manager_plugin/__init__.py
```python
"""Python Session Manager Plugin - AWS Session Manager Plugin implementation."""

__version__ = "0.1.0"
__author__ = "Your Name"
```

#### src/session_manager_plugin/session/__init__.py
```python
"""Session management module."""

from .session import Session
from .session_handler import SessionHandler

__all__ = ["Session", "SessionHandler"]
```

#### src/session_manager_plugin/communicator/__init__.py
```python
"""WebSocket communication module."""

from .websocket_channel import WebSocketChannel

__all__ = ["WebSocketChannel"]
```

#### src/session_manager_plugin/utils/__init__.py
```python
"""Utility functions and helpers."""

from .logging import setup_logging

__all__ = ["setup_logging"]
```

### 5. Setup Development Tools

#### .gitignore
```
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg
MANIFEST

.env
.venv
env/
venv/
ENV/
env.bak/
venv.bak/

.pytest_cache/
.coverage
htmlcov/
.tox/
.cache
nosetests.xml
coverage.xml
*.cover
.hypothesis/

.mypy_cache/
.dmypy.json
dmypy.json
```

#### pyproject.toml tool configurations
```toml
[tool.black]
line-length = 88
target-version = ['py313']
include = '\.pyi?$'

[tool.mypy]
python_version = "3.13"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
disallow_incomplete_defs = true

[tool.ruff]
target-version = "py313"
line-length = 88

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "-v --tb=short"
asyncio_mode = "auto"
```

## Validation Steps

1. **Verify uv setup:**
   ```bash
   uv --version
   uv python list
   ```

2. **Install dependencies:**
   ```bash
   uv sync
   ```

3. **Run basic checks:**
   ```bash
   uv run python -c "import session_manager_plugin; print('Package imported successfully')"
   uv run black --check src/
   uv run mypy src/
   uv run pytest --collect-only
   ```

## Success Criteria

- [x] uv project initialized with Python 3.13.3
- [x] Package structure created following Go implementation patterns
- [x] Development dependencies configured
- [x] Basic import tests pass
- [x] Code quality tools configured and passing

## Next Phase

Proceed to [Phase 2: Core Session Management](01_session_management.md) once all validation steps pass.