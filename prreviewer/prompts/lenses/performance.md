**Performance & resources.** Will this change be noticeably slower, heavier or less scalable than it needs to be on realistic inputs — and did it regress anything hot?

- Algorithmic: nested loops over collections that can be large (O(n²) where a set/dict gives O(n)), repeated linear searches, sorting inside loops, recomputation that should be cached or hoisted.
- I/O: N+1 queries, queries without an index/limit, per-item network calls that could be batched, synchronous blocking calls in async code, missing streaming for large payloads, unbounded reads into memory, excessive logging in hot paths.
- Memory: accumulating lists instead of generators, copying large structures, caches without eviction, retaining references (closures, globals), unbounded queues.
- Concurrency: lock contention, serialising what could run concurrently, thread/process creation per request, missing connection pooling, busy waiting.
- Regressions: a previously lazy/batched/cached path made eager/unbatched/uncached; a hot function (grep callers) made more expensive; new work on startup or import.

Only report issues on paths that plausibly see real load, and quantify ("called once per request", "list can have 10k entries per the fixture"). Micro-optimisations are not findings. Category `performance`.
