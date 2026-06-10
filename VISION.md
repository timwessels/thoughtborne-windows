# Thoughtborne — Vision & Direction

The README explains how to use the tool; this document explains why it exists,
what it is trying to be, and what guides decisions when they have to be made.
It is written for contributors — human or AI — and for anyone deciding whether
this tool is for them. If you are about to make a judgment call about scope,
priorities, or trade-offs, calibrate against this.

## What this is

A hotkey-driven voice-to-text tool for Windows. Press a hotkey, talk, press
another hotkey — and the transcript lands at the cursor position in whatever
app is active, as if you had typed it. It is optimized for German and built
first and foremost for one job: talking to AI.

## Why this exists

Most of my workday is talking to LLMs, and the keyboard is the bottleneck.
I've been running some version of this tool since 2023. It started out of
frustration: the ChatGPT mobile app had voice input, my desktop didn't, and
constantly reaching for the phone just to dictate felt absurd. I searched for
a long time, tried everything on offer, found nothing — so I built it myself.
It's been essential to my workflow ever since; I'd genuinely sooner give up
the physical keyboard than give up voice input. And I built it the way I
still build it today: entirely with AI. I'm an IT person, not a programmer,
and every part of this tool was written with AI assistance. For this project
that's not a footnote, it's the method.

Commercial voice-typing tools have caught up in the last year or two. The
reason I still maintain my own comes down to a few things none of them gets
fully right:

- **The best current German model, always.** I dictate in German at least 95%
  of the time, almost exclusively to AI. I want whatever speech model is
  currently the most accurate for German — and when something better appears,
  I want it integrated, regardless of vendor. Most commercial tools are
  optimized for English first; German is where they tend to fall short.
- **A quality bar high enough to send unread** (see below). I haven't seen
  this level of German accuracy in any finished product.
- **One-press send.** A dedicated hotkey transcribes, inserts, *and presses
  Enter* — entire AI conversations without touching the keyboard.
- **Hallucination filtering.** Speech models produce well-known artifacts
  (Whisper's `Vielen Dank`, Soniox's trailing conjunctions). Most commercial
  tools let them through — my guess is their filtering, if any, targets
  English. Thoughtborne ships model-specific filters for German artifacts,
  built from three years of annotating my own transcriptions.
- **Pay for usage, not a subscription.** You bring your own API keys and pay
  cents. My own last half-year of heavy use — on the order of 150 hours of
  transcription — cost me roughly $10–15. A free path exists too (GROQ's
  free tier), so you can try the tool without paying anyone.

The day a commercial product does all of this better, I'll happily switch.
So far, none does.

## The quality bar: good enough to send unread

This is the central promise, and the standard every model choice and feature
decision is measured against:

**Everything the tool outputs must be good enough to send to an LLM unread.**

I don't proofread my dictation — proofreading would hand back the very time
that voice input buys. That only works if transcripts are reliable at the
level of *meaning*. The real danger is not a missing comma; it is a
meaning-bearing word recognized as a different word, so the LLM confidently
acts on wrong information. A transcript that needs proofreading belongs to a
different class of tool — that's where built-in OS dictation features live,
and it's why they are no substitute.

Consequences:

- The bar disqualifies models. Whisper Large V3 Turbo, for example, doesn't
  clear it for German — I wouldn't send its raw output to an LLM unread.
  Models below the bar can still exist in the lineup (the hotkey-cycled API
  carousel) as utility options, but not as the recommended default.
- Models are judged by hands-on testing with real dictation, not by
  benchmark numbers alone. I re-test the field every few months.
- Above the bar, there is room for trade-offs (see the two modes below).
  Below it, speed is worthless.

## Two modes, two jobs: verbatim vs. polished

Dictation is not one job. Thoughtborne deliberately keeps two kinds of
transcription side by side:

- **Verbatim & instant** (today: Soniox Live, streamed during recording):
  transcribes what you actually said — fillers, hesitations and all — and the
  result is ready the moment you stop, even after ten minutes of talking.
  For talking to LLMs this is a feature twice over: no waiting, and the
  fillers can carry signal about what you mean and how sure you are. Its
  occasional small misrecognitions are the kind an LLM straightens out from
  context — which is why it still clears the bar.
- **Polished & patient** (today: the Soniox upload models — audio is sent
  after you stop): reads like something you would have written — proper
  punctuation, no fillers. Ten minutes of audio take on the order of a
  minute. The right mode for emails and texts meant for humans, or whenever
  wording matters more than waiting.

Both jobs are legitimate; the tool should keep serving both and keep the
choice one hotkey away. (Optional LLM post-processing may later make this
distinction explicit and configurable — see below.)

## Who it's for

People who think technically: IT folks, developers, tinkerers. People who
are comfortable launching a script in a terminal, pasting an API key into a
`.env` file, and reading a short doc. Increasingly also: people who adapt
their tools by telling an AI what to change — "Claude, add this feature" —
which is exactly how this tool is built and why clean, readable code is a
feature here, not an aesthetic preference.

It is *not* aimed at people who want a polished GUI product with guided
onboarding — commercial tools serve them well. The honest ambition: me, my
friends, and the friends of my friends. If strangers find value in it beyond
that, that's a genuine pleasure, and the project should be ready for them —
good setup, good docs, contributions welcome. Watching people pick it up and
extend it would be a reward of its own. But the tool must keep fitting my
own daily use; that is the design anchor.

## What guides decisions

When a decision has to be made and the issue at hand doesn't settle it,
these principles do. Stability (1) outranks the others. Two points below are
not preferences but **hard gates** — the quality floor in (2) and the data
rule in (5): no amount of benefit elsewhere outranks a gate.

1. **Stability first.** This is an essential daily tool, not a playground.
   Recording, transcription, and insertion must simply work; nothing
   essential may break, and `main` stays releasable — I run it every day,
   which doubles as continuous testing. This is explicitly not a
   move-fast-and-break-things project: no feature is urgent enough to
   justify shipping breakage. AI-era development is what makes this cheap:
   throw as many agents, reviewers, and tests at a change as it takes.
   Tokens are cheaper than a broken workflow.
2. **Accuracy over latency — above the hard floor.** The floor is the
   unread bar: nothing below it can be the default or a recommended path,
   no matter how fast. Above it, prefer accuracy when in doubt; I'd rather wait a minute
   for a clean transcript than save fifty seconds and ship one wrong but
   important word. Latency wins where the fast path is the point (Live).
3. **The best German model wins.** No loyalty to any provider or API. The
   model lineup follows current quality for German, re-evaluated hands-on
   every few months — and as of mid-2026 the field is close: several models
   are roughly on par, so the current lineup is simply what tested best.
   Integrating something better — and retiring what it replaces — is core
   maintenance, not scope creep. New backends should fit the pattern —
   bring-your-own-key, usage-based pricing — and must offer a training
   opt-out (see 5). Integration work can be prepared autonomously; lineup verdicts —
   what becomes the default, what gets retired — follow my hands-on testing.
4. **Simple for newcomers, adaptable for owners.** Setup, terminal output,
   and docs should be obvious to someone seeing the tool for the first
   time, and the features the tool already has should be sensibly
   configurable. Beyond that, depth comes not from sprawling config
   surfaces but from open, clean code that users can change — usually by
   telling an LLM what they want. Keeping the code legible *is* the
   configurability strategy. (Legibility is something to preserve when code
   changes for other reasons — not a license to rewrite working code for
   its own sake.)
5. **Your data is not training data.** Audio goes to the transcription API
   you chose, and nowhere beyond that; recordings and transcripts are
   archived locally, nowhere else. Every integrated API must offer at
   least an opt-out from training on user data; APIs that can't are not
   integrated.

## Where it's heading (as of mid-2026)

**Near term — make it work for strangers.** The move to a public repo, with
outside users and contributors explicitly welcome, frames the current work:

- **Newcomer experience:** a setup that holds your hand — guided install,
  API-key onboarding (including where to get keys), a README written for
  strangers, a logo, a friendly introduction.
- **A glanceable terminal:** today's output is log lines that I can read
  because I wrote them. The goal: one look tells you which API/model is
  active, whether something went wrong and what to do about it, and which
  hotkeys you can press. A terminal that *behaves* a bit like a UI — without
  being a GUI. (This is about what the terminal surfaces; the log file
  keeps its diagnostic depth.)
- **A free way in:** since Soniox now requires prepayment before any use,
  the free GROQ path matters for trying the tool out. Adding Whisper Large
  V3 (not just Turbo) gives the free tier a better-quality option.
- **Fewer "you just have to know that" moments:** the small known quirks and
  rough edges should go away; the tool should feel smooth, not folkloric.
  Part of this is honest packaging of the model lineup — the current split
  into two Soniox upload models exists for quality reasons, but a newcomer
  shouldn't need to understand it before dictating.

And then actually ship. Software is never finished, and "it could still be
a bit better" is not a release blocker — at some point you just put it out.

**Later, optional, explicitly not launch-blocking:**

- **Optional LLM post-processing** of transcripts — making the verbatim
  vs. polished distinction an explicit, configurable choice (and giving
  spelling-and-grammar cleanup as a side effect). Today's quality is good
  enough without it; that's why it can wait.
- **English as a real second language.** The same models can handle English,
  but today's configuration is tuned for German end to end — language
  hints, model choice, artifact filters. Closing that gap is on the list;
  it isn't the current focus.
- **A real GUI.** Not ruled out — potentially nice someday. The glanceable
  terminal comes first; hotkeys remain the primary interface either way.

**Explicitly not the ambition:** turning this into a polished mass-market
product. That would be more work than the project wants to carry, and it
would start bending the tool away from how I actually use it.

## Non-goals

- **No real-time on-screen transcription** while speaking. Live *processing*
  yes (that's Soniox Live), live *display* no — the provisional words you
  would see while still speaking don't meet the unread bar (the finalized
  transcript after stopping does), and I don't work that way. Only worth
  revisiting if streaming-display quality ever genuinely clears the bar.
- **No cloud storage** of recordings or transcripts. Local archive only.
- **No local/offline models.** Deliberately API-based — the point is riding
  the quality curve of the best hosted models.
- **No training of own models.**
- **No cross-platform codebase.** Audio devices, global hotkeys, and text
  insertion are deeply OS-specific. The Windows repo stays Windows-only;
  the macOS port lives in its own repo.

## The macOS port

[`thoughtborne-macos`](https://github.com/timwessels/thoughtborne-macos) is
a working port — three transcription backends instead of four, otherwise
analogous. I no longer use a Mac and don't plan to actively maintain the
port; it stays available as-is, and anyone from the community who wants to
carry it forward is welcome to. The Windows repo is the actively maintained
main line.
