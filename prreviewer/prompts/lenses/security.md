**Security.** Could this change be exploited, leak data, or weaken an existing control?

Trace every new input from its source (HTTP params, CLI args, files, env, DB, messages, LLM output) to every sink, and check:
- Injection: SQL/NoSQL, shell (`subprocess` with `shell=True`, string-built commands), template, LDAP, XPath, header/log injection, path traversal (`../`, absolute paths, symlinks), SSRF (user-controlled URLs/hosts), deserialization of untrusted data (pickle/yaml.load/eval/exec), prototype pollution, regex DoS.
- AuthN/AuthZ: endpoints or actions added without the existing auth decorator/middleware; object-level checks missing (IDOR); privilege or tenant boundaries; CSRF; token/session handling.
- Secrets and data: hard-coded credentials, tokens in logs/errors/URLs, PII in logs, insecure defaults, weak randomness (`random` for security), crypto misuse (ECB, MD5/SHA1 for passwords, missing verification), TLS verification disabled.
- Files/resources: world-writable paths, temp-file races, unbounded reads/decompression (zip bombs), unbounded memory/recursion from input, missing timeouts on network calls.
- Supply chain & CI: new dependencies (is the package real, maintained, pinned?), lockfile changes, `curl | sh`, GitHub Actions with `pull_request_target`, overly broad `permissions:`, unpinned third-party actions, secrets exposed to forks.
- Removed or loosened checks: validation, rate limits, CORS, CSP, sanitisation, `verify=False`, `--no-verify`.

Report with category `security`; severity by exploitability × impact. Provide a concrete attack scenario in the explanation. Do not report theoretical issues in code that is unreachable or already protected upstream — check.
