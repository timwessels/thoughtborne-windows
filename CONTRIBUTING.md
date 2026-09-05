# Contributing

Issues, questions and pull requests are welcome, in English or German.

Before proposing a behaviour change, read [VISION.md](VISION.md) (what the
tool is and deliberately is not) and check [DECISIONS.md](DECISIONS.md), the
log of settled product decisions, so a call that was already made is not
reopened by accident. [AGENTS.md](AGENTS.md) holds the working rules for this
repository, for humans and AI coding agents alike: conventions, guardrails,
and how to verify a change.

Run the off-Windows test ladder before opening a pull request
(`python3 run_tests.py`, no Windows needed); anything that touches recording,
insertion or hotkeys also needs a hands-on check on Windows, so say in the PR
what you tried. Keep code, comments and commit messages in English, and add a
CHANGELOG entry under `## [Unreleased]` for anything non-trivial.

Security-relevant findings go through [SECURITY.md](SECURITY.md), not the
public tracker.
