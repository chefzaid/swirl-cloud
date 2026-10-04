# ADR 0026: Drive Installation From One Versioned Input File

- Status: Accepted
- Date: 2026-10-04
- Amended: 2026-10-04 (single `./install.sh` entry point and setup hub)

## Context

The installer collected inputs three ways: interactive prompts, environment
variables under `--yes`, and Ansible's `installer_environment` mapping. Under
`--yes`, some decisions (system prerequisites, K3s reinstall, components) could
not be set at all and silently took prompt defaults. A problem in one input
stopped the run only when its prompt was reached, possibly after host changes.
A terminal UI and later a web front end need one contract to write.

## Decision

- [`scripts/lib/install_inputs.py`](../../scripts/lib/install_inputs.py) declares
  every operator-facing installer input once: section, prompt, type, default,
  conditions, requirements, secret flag and whether express setup asks it. The
  schema is versioned (`SWIRL_CLOUD_INSTALL_INPUTS=1`).
- A per-install file of `KEY=VALUE` lines is parsed as data. Bare,
  single-quoted and double-quoted values are supported; nothing is expanded,
  sourced or executed. Unknown keys, duplicate keys and files readable by group or
  others are rejected. Version pins and service inventories stay in the checked-in
  [`config/platform.env`](../../config/platform.env).
- `install-control-plane.sh --env-file FILE` is the engine. It clears every
  schema input from the calling environment, imports the file, reports every
  schema and credential-policy problem in one list before any change, then runs
  unattended. `--validate-only` stops after that report. The `--yes` path and
  interactive prompts are unchanged; preset decision inputs now skip their
  questions in both.
- [`install.sh`](../../install.sh) is the single entry point. Without arguments
  it opens the setup hub; `--env-file` runs the engine and `--status` runs
  [`verify-platform.py`](../../scripts/verify-platform.py). A bare interactive
  `install-control-plane.sh` opens the hub too; `--prompts` keeps the plain prompts.
- The hub is [`scripts/install-tui.py`](../../scripts/install-tui.py). Besides
  collecting inputs it checks the host and credentials, runs the engine with a
  private log, verifies the result, and starts node enrollment and repository
  import. It uses Python 3 and `whiptail`, both present on stock Ubuntu, so it
  needs no extra runtime before prerequisites are installed. It renders the schema, keeps
  secrets out of process arguments, and writes the file with mode `0600` in a
  `0700` directory outside the checkout. `ansible/install.yml` accepts the same
  file through `installer_env_file`.

## Rationale

One schema keeps the TUI, the engine and future front ends from drifting, and
makes "every question" a property of the schema rather than of each front end.
Parsing values as data closes shell injection through a file that may be edited
by hand or produced by another tool. Reporting all problems before any change
avoids partially configured hosts. `dialog` and curses-based libraries were
rejected: `dialog` is not installed by default, and a Python TUI library would
need a package before the installer can install packages.

## Consequences

- New installer inputs must be added to the schema; the file rejects unknown keys.
- The file holds credentials. Secret resolution from protected files, stdin or
  Vault references, with redaction, remains a separate decision (TODO §23).
- Interactive-only steps outside the installer's own inputs still need a person:
  registrar nameserver delegation in `configure-cloudflare.sh` fails without a TTY.
