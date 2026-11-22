# Security

Synla is version 1 and still a work in progress. Security and privacy are part of the design, not added later:
[docs/security.md](docs/security.md) lists what Synla does to keep your data yours and right, and each point has a test.

## Report a problem

Please don't report a security problem in a public issue. Use **Report a vulnerability** under the Security tab of this
repository, or email una@unadata.dev. Say what you did, what happened, and which version you use (in `pyproject.toml`).
I aim to answer within a week.

## What counts

- Anyone but you reading Synla's pages or files, also another account on the same computer.
- A web page, a window title or a file that makes Synla run code or change your data.
- Data leaving your computer.
- The record book accepting a changed week without saying so.

## Not covered

Someone signed in as you, or with admin rights on your computer, can read your files. No app can protect against that.
Use Synla on a computer you control.

## Versions

Fixes go into the latest version.
