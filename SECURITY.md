# Security policy

This document covers vulnerability reporting and security practices for Cortex.

---

## Supported versions

| Version | Supported |
| :---    | :---      |
| `main`  | Yes       |
| `< 0.1` | No        |

---

## Reporting a vulnerability

Do not open a public issue for security vulnerabilities. Instead, report them privately:

1. Send an email to the project maintainers with the subject `[SECURITY] Vulnerability in Cortex`.
2. Include:
   - A description of the vulnerability and its potential impact.
   - Steps or code to reproduce the issue.
   - The affected subsystem (such as MCP server, Cypher execution, AST parsing, or lifecycle hooks).
   - Any proposed fix or mitigation.

Maintainers will acknowledge receipt within 48 hours and work with you on testing and disclosure.

---

## Security practices

1. **Environment secrets**:
   - Store API keys and database credentials in `.env`.
   - Never commit `.env` files or hardcode credentials into scripts.
2. **Hook safety**:
   - The `cortex-safety-gate` hook (`PreToolUse`) intercepts destructive commands (`rm -rf /`, `mkfs`, `git reset --hard`). Keep this hook enabled in production environments.
3. **Database query safety**:
   - Use parameterized Cypher queries across all database drivers. Never concatenate unescaped input into Cypher strings.
4. **Local port exposure**:
   - FalkorDB (port 6379), Qdrant (port 6333), and the dashboard (port 8004) bind to localhost by default. Do not expose these ports to public networks without an authenticating reverse proxy.
