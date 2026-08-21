# AGENTS.md

## Working Rules

- Understand the existing implementation before changing it.
- Prefer minimal, direct changes over new abstractions.
- Do not add compatibility layers, speculative fallbacks, duplicate mechanisms, or unused configuration.
- Preserve existing behavior unless the task or an authoritative spec explicitly changes it.
- Run relevant tests and checks before declaring completion.
- Do not modify unrelated files.

## Specifications

When a change is governed by a repository specification, read that specification before implementation and follow it as the source of truth.

For Context Manager changes, the authority is:

`docs/spec.md`

Every Context Manager production change must map to applicable `CMV3-*` requirements.

If code reality conflicts with a frozen specification, report:

`SPEC DEVIATION REQUIRED`

Do not silently deviate from the specification.