# Safety and integrity

What Synla does so your data stays yours and stays right. Each point has a test in `tests/testsynla.py`.

## Who can open the pages

- The pages answer on 127.0.0.1 only, and only to requests that name 127.0.0.1 or localhost. That stops DNS
  rebinding.
- Every page needs a key. Synla gives it to the browser it opens, as a cookie that scripts cannot read and other
  websites never send. Without it the page says "Open Synla again". Another account on the same computer has no key.
- Before Synla hands the key to a copy that is already running, that copy must show it knows the key. A program that
  took over the port gets nothing.
- On Windows the port is held exclusively while Synla runs.
- Changes also need the page token, sent as JSON, which other websites cannot send.
- Only Synla's own script runs: the browser checks its fingerprint. Everything shown is escaped, so a window name that
  looks like code stays text. No frames, no forms to other sites, no referrer.

## What goes in

- Every request is checked: dates from 2000 to 2100, projects from your list, kinds of work Synla knows, at most
  1 MB. A bad request gets a plain no, and nothing is half saved.
- Names lose control characters and text direction characters, so nothing hidden or reversed is kept.
- A window name with a broken character is kept with a ?, and never longer than 1000 characters.
- Activity is only read on this computer. An ActivityWatch address that points elsewhere is not used.
- CSV files and the copied table never hold formulas: a cell that starts with = + - or @ gets a ' in front.

## Files

- Every file is written whole or not at all, and the version before is kept as name.bak.
- A damaged file, such as a typo in config.json, is kept as name.damaged-time. Synla uses the last good copy and says
  so at the top of every page.
- Leftovers from a crash or a power cut are deleted after an hour.
- Keys are made once, also when the recorder and the pages start at the same moment.
- Delete everything also deletes the copies, and empties activity.db while the recorder holds it open. If another
  program holds a file, Synla says which.
- Deleted window names are overwritten on disk, not just marked as free.

## The record book

- Sealing takes the lock before it reads the last fingerprint, so two seals at the same moment cannot split the chain.
- A record is always for a whole week, Monday to Sunday.

## When something goes wrong

- The pages show a plain message, never the inside of the program. The details go to errors.log in Synla's folder,
  kept to 50 KB. The recorder notes the same problem at most once an hour.
- One bad moment never stops the recorder, and a clean-up that fails never stops Synla from starting.
- One request at a time, so two clicks at once cannot mix up your answers.
- A clock that is set back never makes negative time.
- Starting with Windows works from any folder name, also one with an apostrophe or ø.

## Not covered

- Someone signed in as you, or with admin rights, can read your files. Use Synla on a computer you control.
- Synla is not for a shared server such as a Citrix or Remote Desktop host, where other people use the same computer.
