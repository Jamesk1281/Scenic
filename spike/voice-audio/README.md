# THROWAWAY — voice guidance audio spike

Not part of the app. Do not merge this branch. It exists so
`docs/voice-guidance-plan.md` §1 can cite something reproducible.

It answers one question: **does `AVSpeechSynthesizer` speak while the app is
backgrounded or the phone is locked, with `UIBackgroundModes: location`
declared and `audio` not?**

The answer is no. Adding `audio` fixes it. See `docs/voice-guidance-plan.md` §1
for the method, the numbers and the caveats, and §12 for how to run this.

## What is here

- `Sources/` — one app, built twice. `SpikeLoc` declares
  `UIBackgroundModes: [location]`; `SpikeLocAudio` declares
  `[location, audio]`. **Identical sources**, one plist key apart, so the
  comparison is a controlled diff. `SpikeRunner` copies `LocationManager`'s
  configuration exactly and speaks from the CoreLocation delegate — the same
  `onFix` path a drive uses, not a timer.
- `Tone/` — a second app holding a long-form playback session, to stand in for
  "music playing from another app". **Never installed**: a free developer
  profile allows three apps on a device and `app.scenic.demo` plus a spike
  variant plus the test runner is already three. The other-audio state is
  therefore unmeasured; see plan §1.4.
- `UITests/` — answers the location prompt and presses Home. Needed because
  `devicectl` will not launch anything on a locked phone and this one auto-locks
  in about a minute. Deliberately declares no app dependency, so the runner does
  not spend a second app slot.
- `speechlen.swift` — offline synthesis on the Mac, used to measure how long
  each real instruction takes to say. Reads phrases on stdin, prints seconds.
  Calibrated against the device to within 2%.
- `measurements/` — the two device logs the plan quotes, and the per-instruction
  durations.

## The two logs, in one line each

`device-location-only.jsonl`: foreground 4/4 spoke; backgrounded 0/19, every
`setActive(true)` throwing `'!pla'`.

`device-location-plus-audio.jsonl`: 23/23 spoke, 16 of them with the screen off.
