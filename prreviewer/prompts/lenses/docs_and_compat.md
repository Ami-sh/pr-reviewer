**Documentation & compatibility.** Can users and downstream code upgrade safely, and will they know what changed?

- Public surface: any change to a function signature, CLI flag, config key, environment variable, HTTP route/response shape, event/message schema, DB schema, file format, or exported name — is it backward compatible? If not, is it versioned, deprecated first, or documented as breaking (changelog, migration note, semver bump)?
- Defaults: changed default values or behaviours silently alter existing users' outcomes — flag them.
- Documentation: README/docs/docstrings/help text/OpenAPI that describe the changed behaviour and are now wrong or incomplete. Only demand docs where the repository keeps them (check what exists).
- Error messages and logs that users rely on (parsed by scripts, searched in runbooks).
- Dependencies: raised minimum versions, dropped platform/language support, new system requirements.
- Data migrations: forward and rollback, idempotency, behaviour during a rolling deploy with mixed versions.
- Deprecations: removed code that had external users; missing deprecation warnings.

Category `docs` (for missing/incorrect documentation) or `design` (for compatibility breaks); severity by how many users break and how silently.
