# Synla

[![tests](https://github.com/unamarie/synla/actions/workflows/tests.yml/badge.svg)](https://github.com/unamarie/synla/actions/workflows/tests.yml)

Synla fills in your timesheet from what you actually did on your computer. It also shows the work that never makes it
into a plan: helping colleagues, firefighting, chasing information and fixing other people's data.

*Synla* comes from the Nynorsk *synleg*, "visible".

![Review a day](docs/review-day.png)

## What it does

- **Works out what each block of time was for.** It looks at file names, folders, email threads and chat names, not
  just which app was open. Then it asks you only about the blocks it isn't sure of.
- **Learns from your answers.** Every answer becomes a clue for next time. In the demo week, Monday asks about 10 of
  19 blocks. By Friday it asks about 1 of 26, and none of the blocks it was sure about were wrong.
- **Makes unplanned work visible.** The weekly summary shows how much of your time went to helping, firefighting,
  chasing and fixing data, where it came from, and how much happened outside working hours.
- **Produces the timesheet.** Hours per project and day, rounded to 15 minutes, exported as CSV.

![Week summary](docs/week.png)

## Your data stays yours

- ActivityWatch records the activity. Synla reads it from `localhost` and keeps its own files in `~/.synla`.
- Nothing is sent anywhere. The review page listens on `127.0.0.1` only.
- The weekly summary and the export remove email addresses, phone numbers and long numbers.
- Your answers are deleted after `keepDays` (90 by default).
- Synla is built for you, not your employer. Don't install it, or ActivityWatch, on a work computer without your
  employer's written approval.

## Try it with a made-up week

You need Python 3.10 or newer. Nothing else is required. On Windows, type `py` where it says `python`.

```
cd synla
python -m synla demo
```

This opens the review page in your browser with a fictional week. Answer some blocks on Monday, then look at Tuesday.

## Use it for real

1. Install [ActivityWatch](https://activitywatch.net) and let it run for a day.
2. Open `~/.synla/config.json` and add your projects with a few keywords each:

   ```json
   "projects": {
     "Data platform": {"keywords": ["lakehouse", "pipeline"]},
     "Internal": {"keywords": ["timesheet"]}
   }
   ```

3. Run `python -m synla` and answer the orange blocks. Click **Approve the sure ones** for the rest.
4. On Friday, open **Week summary** and use **Timesheet CSV**.

| Command | What it does |
|---|---|
| `python -m synla` | Opens the review page for today |
| `python -m synla week` | Prints this week's summary as text |
| `python -m synla export --date 2026-11-02` | Writes that week's timesheet as CSV |
| `python -m synla demo` | Tries Synla with a made-up week, kept in its own folder |

## How it decides

The steps run in this order:

1. **Segments.** Window events from ActivityWatch, with idle time cut out (`blocks.py`).
2. **Clues.** Each window title is split into clues: the exact window, file name, folder, email subject, words, and
   signals such as "reads like a question" or "sounds urgent" (`context.py`).
3. **Guess.** Each clue votes for a project and a kind of work (`classify.py`). The votes come from two places:
   - your keywords and a few built-in rules
   - your earlier answers

   A clue that always meant the same thing counts fully. A clue seen everywhere, such as "Excel", counts for little.
   Confidence is how clearly the winner beats the runner-up. Below 70 %, Synla asks you.
4. **Blocks.** Segments in a row with the same guess are grouped into one block. Short, unclear glances are folded
   into the block before them.
5. **Quality.** Every answer is logged in `corrections.jsonl`, so Synla can report how often it was right before you
   answered.

Run the tests with `python -m pytest`.

## Next

- Leading indicators for workload: context switches, focus blocks and after-hours work over time, scored green,
  yellow or red, with warnings before a week goes bad.
- Planned vs actual: compare against your calendar.
- An optional local language model for windows with no clues, with titles redacted first.

Built by [Una Marie S. Goksøyr](https://unadata.dev). Code under the MIT licence.
