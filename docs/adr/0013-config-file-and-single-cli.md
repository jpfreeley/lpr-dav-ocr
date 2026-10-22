# ADR 0013: One TOML config, environment overrides, and one `lpr` command

- Status: Accepted
- Date: 2026-10-22

## Context

The first working version was a set of scripts with paths, a camera identifier, a host name and thresholds written in the
code. Packaging it for others requires removing everything specific to one site, without losing the ability to run
unattended.

## Decision

- All site-specific values live in a TOML file (`~/.config/lpr-dav-ocr/config.toml`, or `$LPR_CONFIG`), with a documented
  template in `config/config.example.toml`. Precedence: defaults, file, environment, flags.
- One command, `lpr`, with subcommands, installed by `pip install`. Helper shell scripts call `lpr`.
- Per-user state (watchlist, topic, models) is under a local `home` folder, never in the repository.
- Nothing in the code or documentation names a camera, host, user or path. Documentation uses placeholders.
- A guard script, `scripts/check-private-data.sh`, and `.gitignore` keep real data out of the repository.

## Consequences

- A new user edits one file. The code has no hidden defaults that point at someone's share.
- Configuration is read when modules are imported, so a long-running process does not see later edits.
- Tests run with a temporary home and no real config, so they cannot touch real state.
