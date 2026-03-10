# CANN Static Check Rules

Common rules flagged by codecheck / codecheck_inc in CANN CI pipeline.
Used by ci-analyzer agent to understand and fix violations.

## Naming Conventions

- **G.NAM.01**: Variable names must use camelCase (local) or snake_case (member).
- **G.NAM.02**: Function names must use CamelCase.
- **G.NAM.03**: Macro names must use ALL_CAPS_WITH_UNDERSCORES.
- **G.NAM.04**: Constants must use `k` prefix + CamelCase (e.g. `kMaxBufferSize`).

## Code Style

- **G.FMT.01**: Line length must not exceed 120 characters.
- **G.FMT.02**: Use 4 spaces for indentation, no tabs.
- **G.FMT.03**: Opening brace on same line for functions and control structures.
- **G.FMT.04**: No trailing whitespace.

## Safety

- **G.SEC.01**: Do not use `strcpy`, `sprintf` — use safe alternatives.
- **G.SEC.02**: Check return values of memory allocation.
- **G.SEC.03**: Validate pointer before dereference.

## Memory

- **G.MEM.01**: Match every `new` with `delete`, every `malloc` with `free`.
- **G.MEM.02**: Use smart pointers where ownership transfer occurs.
- **G.MEM.03**: Do not return references/pointers to local variables.

## Common HCCL-Specific Rules

- **H.COMM.01**: All public APIs must validate input parameters.
- **H.COMM.02**: Log level must match severity (ERROR for failures, INFO for normal).
- **H.COMM.03**: Thread-shared data must be protected by mutex or atomic.

---

**Note:** This file will be expanded as new rules are encountered in CI.
When a new rule ID appears in CI output that is not listed here, add it.
