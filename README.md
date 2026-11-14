# Synla

[![tests](https://github.com/unamarie/synla/actions/workflows/tests.yml/badge.svg)](https://github.com/unamarie/synla/actions/workflows/tests.yml)

Synla fills in your timesheet from what you actually did on your computer. It also shows the work that never makes it
into a plan: helping colleagues, firefighting, chasing information and fixing other people's data.

It is built for the employee. Your data stays on your computer, under your control, and Synla has nothing an
employer could use to monitor anyone.

*Synla* comes from the Nynorsk *synleg*, "visible".

![Review a day](docs/review-day.png)

## What it does

- **Works out what each block of time was for.** It looks at file names, folders, email threads and chat names, not
  just which app was open. Then it asks you only about the blocks it isn't sure of.
- **Answer once for the whole day.** "Biggest unsorted windows" lists the windows behind most of the unsorted time.
  One answer covers every stretch of that window today. A block that mixed two things can be split.
- **Learns from your answers.** In the demo week, Monday asks about 10 of 19 blocks. By Friday it asks about 1 of 26,
  and none of the blocks it was sure about were wrong. A window you only glanced at is not learned from.
- **Keeps meetings.** In a call you don't touch the keyboard, so other trackers count you as away. Synla knows when
  you are in a call and keeps that time.
- **Makes unplanned work visible.** The weekly summary shows how much time went to helping, firefighting, chasing and
  fixing data, where it came from, and how much happened outside working hours.
- **Produces the timesheet.** Hours per project and day, rounded to 15 minutes, exported as CSV.

![Week summary](docs/week.png)

## Built for the employee

**Yours only.** Synla has no account, no server, no admin mode and no way to collect or compare data from several
people. Nothing leaves your computer unless you copy it out yourself, for example the weekly summary.

**What it records.** The app and title of the window in front, and when. Whether you are at the computer. Whether an
app is using the microphone, so calls are not counted as away.

**What it never records.** Keystrokes, screenshots, the clipboard, file contents, sound or web addresses.

**Private windows.** Password managers, private browsing, online banking and health sites are stored as "Private",
with no name. You choose the list in `config.json`. You can pause recording at any time.

**Protected and short-lived.**
- Data lives in `%LOCALAPPDATA%\Synla`, which stays on your computer: it is not part of a roaming profile and is not
  synced to OneDrive.
- On Windows, window names are encrypted with your Windows login, so other accounts, a copied disk or a backup see
  scrambled bytes.
- What Synla learns is stored as one-way codes, never as readable file names or email subjects.
- Window names are deleted after 30 days. After that only the project, the kind of work and the time remain, so your
  timesheet stays while the details about colleagues and customers are gone. Answers and the log go after 90 days.
- The **Your data** page shows everything that is stored, and deletes all of it with one click.

**The review page is closed to other websites.** It only answers on 127.0.0.1 and only accepts changes that carry a
secret from Synla's own page, so a web page you have open cannot read or change your data.

**Limits, said plainly.**
- On a computer your employer owns and manages, IT can in principle reach any file, and a domain administrator can
  recover Windows encryption keys. Use Synla on your own computer, or get your employer's written approval first.
- Rules for monitoring at work (in Norway, chapter 9 of the Working Environment Act) are about measures an employer
  starts. Synla is something you start and control, but using it on a work computer still falls under your
  employer's IT rules. This is not legal advice.
- If you use ActivityWatch, it keeps its own history. Synla cannot delete it; you can, in ActivityWatch.

## Three ways to record

| Way | For | Notes |
|---|---|---|
| **Synla's own recorder** | Windows (default) | Nothing else to install. Synla controls what is stored and for how long. |
| **ActivityWatch** | Windows, Mac, Linux | Used automatically if it is running. Its browser extension adds web addresses. |
| **Demo week** | Trying it out | A made-up week, kept in its own folder. |

With `"source": "auto"` (the default), Synla uses its own recorder when it has data for the day, and ActivityWatch
otherwise.

## Try it with a made-up week

You need Python 3.10 or newer. Nothing else is required. On Windows, type `py` where it says `python`.

```
cd synla
python -m synla demo
```

## Use it for real (Windows)

1. Start the recorder: `python -m synla autostart on`. From now on it starts when you sign in to Windows, with no
   window. To start it right away without signing out, run `python -m synla record` once.
2. Open `%LOCALAPPDATA%\Synla\config.json` and add your projects with a few keywords each:

   ```json
   "projects": {
     "Data platform": {"keywords": ["lakehouse", "pipeline"]},
     "Internal": {"keywords": ["timesheet"]}
   }
   ```

3. At the end of the day, run `python -m synla`. Answer the biggest unsorted windows first, then the orange blocks,
   then click **Approve the sure ones**.
4. On Friday, open **Week summary** and use **Timesheet CSV**.

| Command | What it does |
|---|---|
| `python -m synla` | Opens the review page for today |
| `python -m synla record` | Runs the recorder in this window (Windows) |
| `python -m synla autostart on` | Starts the recorder at sign-in; `off` stops that |
| `python -m synla pause 60` | Pauses recording for 60 minutes; `resume` starts it again |
| `python -m synla week` | Prints this week's summary as text |
| `python -m synla export --date 2026-11-02` | Writes that week's timesheet as CSV |
| `python -m synla demo` | Tries Synla with a made-up week |

## How it decides

1. **Segments.** Window activity with away time cut out. Calls keep their time, up to two hours without input.
2. **Clues.** Each window title is split into clues: the exact window, file name, folder, email subject, words, and
   signals such as "reads like a question", "sounds urgent" or "in a call".
3. **Guess.** Each clue votes for a project and a kind of work, from your keywords, a few built-in rules and your
   earlier answers. A clue that always meant the same thing counts fully; a clue seen everywhere, such as "Excel",
   counts for little. Below 70 % confidence, Synla asks you.
4. **Blocks.** Stretches in a row with the same guess form one block. Short, unclear glances join the block before.
5. **Quality.** Synla measures how often it was right only on blocks you checked yourself. Bulk approvals don't count
   as right; if you change one later, it counts as a miss.

Run the tests with `python -m pytest`. They run on Linux and Windows on every push.

## Next

- Workload indicators: share of unplanned work, switches, focus time and after-hours work, scored green, yellow or
  red per week, with the trend over months.
- Planned vs actual: compare against a calendar export.
- Rules you can see and edit: "files in this folder belong to this project".

Built by [Una Marie S. Goksøyr](https://unadata.dev). Code under the MIT licence.
