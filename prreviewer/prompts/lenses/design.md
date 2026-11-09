**Design & maintainability.** Will the next engineer (or the next agent) be able to understand, extend and safely change this code — and does it fit how this repository is built?

- Fit: does the change follow the repository's established patterns (module layout, error-handling style, logging, configuration, dependency injection, naming of public things)? Compare with neighbouring code and CLAUDE.md/CONTRIBUTING rules. Reinventing an existing helper is a finding — grep for the utility that already does this.
- Boundaries: is logic placed in the right layer (no business rules in handlers/CLI, no I/O in pure functions)? Are new public APIs minimal and coherent? Hidden coupling (globals, module-level state, import-time side effects)?
- Complexity: functions doing too many things, deep nesting, duplicated blocks that should be one function, flags that switch behaviour, magic numbers/strings without a named constant, clever code without a comment explaining why.
- Error contracts: are failures surfaced with the right type and message, or swallowed? Are partial failures left in a consistent state?
- Evolution: dead code, commented-out code, leftover debug output, TODOs without an issue link, configuration that cannot be changed without a code edit, migrations without a rollback path.
- Comments/docstrings only where the repository expects them (public API, non-obvious decisions); do not demand comments on obvious code.

Report with category `design` (or `style` only when a repo convention is violated). Severity is usually `medium`/`low`; use `high` only when the design will cause real bugs or block a stated goal. No bikeshedding.
