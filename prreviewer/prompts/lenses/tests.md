**Tests.** Is the changed behaviour actually tested, and do the tests prove what they appear to prove?

- Coverage of the change: for every new branch/behaviour/bug fix in the diff, find the test that would fail without the change. If none exists, that is a `test_gap` (severity by risk). Name the missing case precisely ("no test for empty list input to `parse()`").
- Test quality: do assertions check the specific outcome (value, error type, side effect) rather than just "no exception"? Are negative paths and boundaries covered? Are error messages/exit codes asserted where they matter?
- Test honesty: mocks/stubs must not replace the unit under test; fixtures must represent realistic data; time, randomness, network, filesystem and environment must be controlled (otherwise flaky). Watch for order-dependent tests and shared mutable fixtures.
- Changed tests: when an existing test was modified, read the old version (`git show origin/{{base_ref}}:<path>`) and decide whether the change is a legitimate spec update or an accommodation of a regression.
- Repository conventions: test layout, naming, use of the project's fixtures/helpers, parametrisation, markers. Follow what CLAUDE.md/CONTRIBUTING/the existing suite do.
- Determine whether the tests in the diff would actually run under the project's test command (collection patterns, `__init__.py`, `conftest`, build tags, `describe` nesting) — a test that never runs is a `test_gap` with high severity.

Categories you emit: `test_gap`, `reward_hacking` (if tests were gamed), `bug` (a test that asserts the wrong thing).
