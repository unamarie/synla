# Synla: a note for IT

Synla is a personal time tracker. It runs only on the employee's own Windows session, keeps its data on that
computer, and has no server, account or admin view. The code is open source: <https://github.com/unamarie/synla>.

## What it reads

| What | How | How often |
|---|---|---|
| Program name and title of the window in front | `GetForegroundWindow`, `GetWindowTextW`, `QueryFullProcessImageNameW` | Every 5 seconds |
| Seconds since the last keyboard or mouse input | `GetLastInputInfo` (a number, no keys) | Every 5 seconds |
| Whether Teams, Zoom, Webex, Slack or Skype is using the microphone, only if the user turned this on | Reads `HKCU\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone`, the list behind the microphone icon in the taskbar | Every 5 seconds |

## What it never does

- No keyboard or mouse hooks, no keystrokes, no screenshots, no clipboard, no file contents, no audio.
- No web addresses: only the browser tab title.
- No network traffic. Synla's pages listen on 127.0.0.1 only, on a port held exclusively, and open only with a key
  that Synla gives the browser it starts. If ActivityWatch is installed, Synla reads it on localhost:5600 and never
  from another computer.
- No admin rights, services, drivers or scheduled tasks.

## What it writes

| Where | What |
|---|---|
| `%LOCALAPPDATA%\Synla` | All data. Not roaming, not synced to OneDrive. Window titles and keys encrypted with DPAPI for the current user. Problems are noted in `errors.log` there, kept to 50 KB. |
| `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, value `Synla` | Starts the recorder at sign-in (`pythonw.exe`), only if the user chooses it on Synla's setup page or runs `python -m synla autostart on` |
| `HKCU\Software\Classes\synla` | The link `synla:open` that the end of day notification opens |
| `HKCU\Software\Classes\AppUserModelId\Synla` | The name shown on the notification |

Retention: window titles 14 days, answers 90 days, weekly records (no window titles) 5 years. Names in Teams chats,
email addresses and Dynamics 365 contacts are hidden before anything is stored.

## What security tools may notice

- A Python process that reads window titles and idle time, started at sign-in. The **Start recording** button starts
  the same `pythonw.exe` command, with no window.
- The end of day reminder starts `powershell.exe` (hidden, with an encoded command) to show a standard Windows
  notification. To avoid it, set `"reminder": "off"` in `config.json`; the rest works without it.

## Safety checks

[security.md](security.md) lists what Synla does about access, input, files and errors, each with a test.

## Requirements and removal

- Python 3.10 or newer, standard library only. No other packages.
- Remove: `python -m synla autostart off` (removes all three registry entries), then delete `%LOCALAPPDATA%\Synla`
  and the Synla folder.
- To approve one exact version, use a release tag or commit in the repository.
