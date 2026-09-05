# Security policy

Thoughtborne runs entirely on your own machine. It opens no listening port,
has no server side, no account and no telemetry; the only network traffic is
the audio it sends to the transcription provider you configured (Soniox or
Groq, over TLS) and the installer's download of the release itself. Your API
keys live in the local `.env`, your recordings and transcripts in the local
`history/` folder. The threat model is therefore small, but real: a bug that
exposes a key, leaks a recording or transcript, or makes the installer,
launcher or uninstaller do something you did not ask for.

## Reporting a vulnerability

Please do not open a public issue for anything that could put a user's keys,
recordings or transcripts at risk. Use one of these two private routes:

- **GitHub private vulnerability reporting:** *Security* tab of this
  repository → *Report a vulnerability*.
- **Email:** t@wssls.net

Include what you found, how to reproduce it, and which version or commit you
looked at. Reports are read as they come in; please allow up to a week for an
acknowledgement. Fixes ship as a normal release with a CHANGELOG entry; you
will be credited there unless you prefer not to be.

## Scope

In scope is everything in this repository: the tool, the settings app, the
installer (`setup.ps1` / `setup.bat`), the launcher, the uninstaller, and
the release build. Out of scope are the transcription providers themselves
(report those to Soniox or Groq directly) and Windows' own limits, such as
hotkeys and text insertion not reaching elevated windows, which are
documented behaviour, not defects.

## Supported versions

Only the latest release on the
[Releases page](https://github.com/timwessels/thoughtborne-windows/releases)
receives fixes. The installer's one-liner and `setup.bat` always fetch that
release, so running either again brings an installation up to date.
