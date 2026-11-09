**Correctness.** Does the new code compute the right thing on every path it can be reached from?

Check, for each changed function/branch:
- Logic and boundaries: off-by-one, inclusive/exclusive ranges, empty inputs, None/null/zero/negative values, unicode and whitespace, timezone/locale, integer overflow and float comparison, truthiness of 0/""/[].
- Control flow: early returns that skip cleanup, missing `else`, fallthrough, exceptions swallowed or re-raised with lost context, retries without backoff/limits.
- State and ordering: mutation of shared/default-arg objects, iteration while mutating, stale caches, race conditions and non-atomic read-modify-write, resource leaks (files, sockets, locks, transactions).
- Contracts: do callers (grep them!) still get what they expect — return types, raised exceptions, optional fields, ordering, encoding? Did a signature change ripple everywhere? Are new error paths handled by callers?
- Data: schema/migration consistency, serialization round-trips, nullability, units.
- API misuse: library calls with wrong argument semantics, deprecated/incorrect flags, async functions not awaited.
- Removed code: was anything deleted that something else still depends on?

Categories you emit: `bug` (and `question` when behaviour is ambiguous and must be clarified before merge).
