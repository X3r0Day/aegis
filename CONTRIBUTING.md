# Contributing

Thanks for looking at Aegis. This is a small project with a simple rule: keep
it honest and keep it working.

## Getting set up

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
HF_HOME=laya/models/hf .venv/bin/python laya/download_models.py english typed-decisions
GUARD_FASTPATH=1 .venv/bin/python guard/app.py
```

The console is at http://127.0.0.1:8978/console. First visit asks for a
password. The model checkpoints are large, so they are not in the repository
and the download above is the only setup step that takes real time.

## Before you open a pull request

Run whatever is relevant to your change:

```bash
.venv/bin/python guard/tools/guard_eval.py        # injection eval, expect 22/22
.venv/bin/python guard/tools/abuse_demo.py        # synthetic client profiles
.venv/bin/python guard/tools/abuse_eval.py        # abuse bench across checkpoints
```

If the change touches the console, open it and click through overview,
decisions, clients, devices and agent. If it touches the agent, use `run now`
and check the action table. If it touches the fast path, run
`guard/tools/fastpath_bench.py` and include the numbers in the pull request.

## What a change should include

- A short explanation of the problem it solves, not a list of files.
- The commands you ran to verify it and what they printed.
- For behaviour changes in the detectors, the eval results before and after.
- For console changes, a screenshot if the layout moved.

## Style

- Python: standard library first, type hints where they help, no new heavy dependencies without a reason.
- JavaScript: plain browser code, no build step, no framework.
- Text and comments: plain English, no em dashes, no marketing words.
- Commit messages: describe what was added or changed, keep them short.

## Things to keep out of the repository

- Model weights and the HuggingFace cache (`laya/models/` is ignored).
- The runtime database with sessions, decisions and blacklist rules (`guard/data/` is ignored).
- Any key, token or password. Use environment variables or the console settings.

## Security issues

Do not open a public issue for a bypass or a vulnerability. See
[SECURITY.md](SECURITY.md).

## License

By contributing you agree that your work is released under the MIT license
found in [LICENSE](LICENSE).
