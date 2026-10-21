# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

Module dependency graph: @docs/architecture.md

## Concepts

Domain model (volumes, endpoints, syncs, snapshots, the backup graph, availability): @docs/domain.md

Configuration reference (every config field and validation rule): @docs/config-reference.md

Runtime behavior, design decisions, and external commands reference: @docs/internals.md

## Guidelines

Shared iglootools guidelines come from the `iglootools` plugin, enabled in
`.claude/settings.json` and installed once per machine: @docs/setup-development-environment.md

## Build & Test Commands

Instructions on how to run unit and integration tests, the testing strategy, and formatting and linting checks: @docs/building-and-testing.md

## Releasing and Publishing

Instructions on how to create new releases and publish the package to PyPI: @docs/releasing-and-publishing.md