# Local Tools

This repo now includes small local tools behind `devctl.py`. They are intentionally simple, logged, and config-driven.

## Storage

Runtime data is local and excluded from Desktop sync:

```text
data/notes/
data/todos/
data/briefs/
```

Notes are markdown files plus a JSONL index. Todos are JSON. Daily briefs are markdown reports. The storage helper lives in `assistant/memory/json_file.py`.

## Config

Tool settings live in `config/settings.toml`:

```toml
[tools.notes]
[tools.todos]
[tools.file_search]
[tools.daily_brief]
```

Use config for paths, ids, statuses, priorities, limits, approved search scopes, safe suffixes, exclude directories, sensitive-name patterns, and brief naming.

## Commands

Notes:

```powershell
python .\devctl.py notes add --title "Idea" --body "Short local note" --tag personal
python .\devctl.py notes list
python .\devctl.py notes search --query "idea"
python .\devctl.py notes show <note-id>
```

Todos:

```powershell
python .\devctl.py todos add --title "Review logs" --details "Check warnings" --priority normal
python .\devctl.py todos list --status pending
python .\devctl.py todos done <todo-id>
python .\devctl.py todos reopen <todo-id>
python .\devctl.py todos cancel <todo-id>
```

Approved-folder search:

```powershell
python .\devctl.py files search --scope personal --query "OpenClaw"
```

Daily brief:

```powershell
python .\devctl.py brief daily --show
```

## Safety

- Notes and todos only write inside configured `data/` paths.
- File search is read-only and limited to configured approved scopes.
- Sensitive file-name patterns such as `.env`, key, token, cert, and secret-like names are skipped.
- Tool logs store action metadata and avoid private message bodies by default.

## Tests

Run:

```powershell
python -m unittest discover -s tests -v
```
