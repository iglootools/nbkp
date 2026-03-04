# Conventions

## General Coding Conventions
- **Functional Style**: 
  - Prefer functional programming style over procedural style. Use pure functions and avoid side effects when possible.
- **Charsets**: 
  - UTF-8 everywhere.
- **Time Management**
  - UTC for all timestamps
  - Do not generate the current timestamps directly inside the core logic: pass the timestamps from the higher-level functions, tests, and other entry points.
- **Mocks**
  - Avoid use of mocks when the values can be passed as a parameter (e.g. time)
- **Console Output**
  - Do not hardcode indents in strings, compute the indent at the call site
- **Version Management**
  - Pin specific versions of all dependencies or use a lock file (e.g. poetry.lock) to ensure reproducible builds and avoid issues with breaking changes in dependencies.
  
    ```bash
    # examples
    mise use --pin pipx:poetry
    ```
- **Github Workflows**
  - Whenever safe (i.e. not affecting production), enable `workflow_dispatch` and `repository_dispatch` to allow manual triggering of workflows from the GitHub UI or CLI, which is useful for testing and debugging.
  - Use OpenID Connect (OIDC) authentication for publishing to PyPI, and set up a separate workflow for testing releases to Test PyPI. This allows testing the release and publish process without affecting the real PyPI index, and provides more detailed logs for debugging.
- **Command Line**
  - When calling external commands, build the command lines as lists of arguments instead of strings to avoid issues with quoting and escaping.
- **Testability**
  - Expose exceptions/errors as structured data classes and perform the assertions on the structured output in tests instead of matching against raw error message strings. This allows for more robust tests that are not brittle to changes in error message formatting.

## General Python Coding Conventions
- **Typing**: Use type annotations for all functions and methods, including return types. Use `mypy` for static type checking.
- **Data Classes**: 
  - All serialized model objects are frozen pydantic dataclasses, immutable once created.
  - Other data classes should also be frozen.
- **Formatting**: 
  - 79 characters (black + flake8).
- **Python Version**: 3.14 (mypy target and black target).
- **Control Flow**
  - Prefer match-case over if-elif-else chains
  - Prefer comprehensions and built-ins (map, filter) over manual loops when appropriate. 
  - Avoid `continue` in loops, and prefer filtering with comprehensions or built-ins instead.
  - Prefer explicit if/else syntax over implicit else 

## Application-Specific Coding Conventions
- **Naming Conventions**
  - `kebab-case` for CLI commands and config keys
- **CLI**
  - Use `typer` for CLI implementation (argument parsing, formatting, etc.)
  - Provide both human-readable and JSON output formats for all commands, with human-readable as the default.
  - Provide ability to pass a config file to all commands
  - Provide a dry-run parameter for all data-mutating or long-running operations
- **Testing**
  - No real rsync/ssh/btrfs calls in unit tests - use mocks instead. Docker-enabled integration tests cover the real interactions.
  - Generate YAML test data using the Pydantic data models and `model.model_dump()` instead of hardcoding YAML strings. 
    This ensures the test data is always valid and consistent with the models.
- **Domain Logic Consistency**
  - When making changes to the config schema/models or status checks, make sure to update:
    - The demo CLI (`nbkp/democli.py`) to generate new test data that reflects the changes, and update the expected outputs in `testdata.py` if necessary.
    - The `cli` CLI app to support the new functionality, and update the formatting logic in `outputs.py` if necessary.
      - `sh` command: 
      - Ensure to add comments in the codebase to describe which choices have been made with regard to which of the original (`run`) functionality has been preserved vs dropped
      - When adding functionality to the `run` command, make sure to also add it to the `sh` command, or explicitly document why it's not applicable.
  - When adding a dependency on an external tool (e.g. `stat`, `findfmt`), add a check for the tool in the CLI app and provide a clear error message if it's not found. 
