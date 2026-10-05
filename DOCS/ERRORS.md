# Errors

Failed approaches and difficult bugs that are worth remembering.

Log a failure when it took more than two attempts, the root cause was somewhere other than the symptom, the behavior is environment-specific and will recur, or a reasonable next approach would fail the same way. This is not a bug tracker. Ordinary bugs found and fixed quickly do not belong here.

## 2026-10-05

### Note: pulpie 0.0.2 breaks with selectolax 1.0

What did not work: A fresh install resolved `selectolax` 1.0.0. The backend started, but the model failed to load with `ImportError: Modest backend is deprecated since selectolax 1.0`, because `pulpie/simplify.py` imports `selectolax.parser.HTMLParser`. The older `pulpie-ui` venv worked only because it already had selectolax 0.4.11.

What worked instead: Pinning `selectolax>=0.3,<1.0` in `pyproject.toml`.

Note for next time: Remove the pin only after a pulpie release moves to `selectolax.lexbor`, and test with a fresh venv, not an existing one.
