# Contributing to Prism Desktop

Thanks for helping out! Bug reports, translations and pull requests are all welcome.

## Running from source

You need Python 3.11 or newer and a Home Assistant instance with a
[long-lived access token](https://www.home-assistant.io/docs/authentication/#your-account-profile).

```bash
git clone https://github.com/lasselian/prism-desktop.git
cd prism-desktop
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

When run from a checkout, `config.json` is created next to `main.py`, so your
development settings stay apart from an installed copy.

The code is laid out in three layers (`core/`, `services/`, `ui/`); see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for a map, and
[docs/platforms.md](docs/platforms.md) before touching anything that differs per
operating system.

## Code style

Formatting and linting use [ruff](https://docs.astral.sh/ruff/), configured in
`pyproject.toml`. CI runs both on every pull request.

```bash
pip install ruff==0.8.6
ruff format .
ruff check .
```

To have this run on every commit: `pip install pre-commit && pre-commit install`.

Some habits the code base follows:

- **Keep functions small.** CI fails a function whose complexity goes above 15;
  split it into named steps, or use a lookup table for per-type behaviour.
- **Never block the event loop.** Network calls are `async`; slow or blocking
  work goes to `loop.run_in_executor` or a `QThread`. Start coroutines from Qt
  slots with `_create_task_safe()` in `main.py`.
- **Let `asyncio.CancelledError` propagate**; don't catch it to end a loop
  quietly.
- **`core/` stays free of `ui/` and `services/` imports** (CI checks this).

## Translations

All user-facing text goes through `t("some.key")` and lives in
`translations/en.json`. Add new keys there; other languages fall back to
English until they are translated. To add or improve a language, edit or copy
a file in `translations/`.

## Pull requests

- Branch from `main` and keep a pull request to one change.
- Fill in the pull request template: what changed and why, a screenshot for UI
  changes, and how you tested it, including **which operating systems you tried
  it on**. Much of the app behaves differently on Windows, Linux and macOS.
- Write commit messages that say what changed and why.

## Reporting bugs

Open an issue with your OS, Prism version (Settings shows it), what you did
and what happened. The log helps a lot: `prism.log` sits next to
`config.json`.

Security problems are different: please report them privately, as described in
[SECURITY.md](SECURITY.md).
