# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

Module dependency graph: @docs/architecture.md

## Concepts

Domain model (volumes, endpoints, syncs, snapshots) and configuration reference: @docs/concepts.md

Runtime behavior, design decisions, and external commands reference: @docs/internals.md

## Guidelines and Workflow

Before writing any code, apply the guidelines at write time, not as a post-hoc review. Walk the implementation checklist item by item before considering a task done.

Common guidelines (shared across iglootools projects):

@../common-guidelines/coding.md
@../common-guidelines/python.md
@../common-guidelines/tooling.md

Project-specific guidelines: @docs/guidelines.md

Implementation checklists: @docs/implementation-checklists.md

## Code Navigation

Answer symbol questions with the Pyright LSP, not with text search: where something is defined, what
references or calls it, what type it returns, what a module contains. The `pyright-lsp` plugin is
enabled at project scope in `.claude/settings.json`. The same name appears in several modules here
(`show`, `check`, `prune_snapshots`, `run_remote_command`), so a symbol answer must come from the
import graph rather than from text matches.

Reach for `grep`/`Glob` when the target is not a resolvable Python symbol — string literals, config
keys, YAML/TOML, comments, filenames, or a name that may not resolve. The LSP does not jump into
`site-packages` either, so third-party definitions still need the file read directly.

## Build & Test Commands

Instructions on how to run unit and integration tests, as well as formatting and linting checks: @docs/building-and-testing.md

## Releasing and Publishing

Instructions on how to create new releases and publish the package to PyPI: @docs/releasing-and-publishing.md