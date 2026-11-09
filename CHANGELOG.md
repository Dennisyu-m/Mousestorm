# Changelog

## 3.1.0

### Operations come from evidence
- Nothing in the film is drawn from a random list any more. Each object is matched against the real primitives, and every patch is named after the displacement the film really plays on it, with measured values.
- Objects that are exactly a primitive are simply added and placed. Repeated shapes are shown as duplicates. Modifiers play the change they cause.
- Move, rotate and resize replay the object's real transform and are skipped when the object has none. Texture paint is only shown when the file has a painted texture or vertex colours.
- The log panel and tool shelf follow the actual operation.

### Framing
- The subject is found by itself: kilometre-wide ground slabs, distant terrain and the planting around a building no longer count, so the camera frames what matters. The file's own camera sets the main angle when it is aimed at the subject (`camera.use_scene`).
- The ground is built first, then the subject, then what surrounds it.

### Hardware
- `--device auto` is the new default: CPU, memory and GPUs are detected, a busy GPU is left alone, and frame workers are limited to what fits in memory. The app has a device picker with a CPU usage slider. `mousestorm devices` shows what was found.

### Automatic pacing
- Small adjustments play several times faster than large changes, measured from how much of the model each one changes. `pace.tweak_speed` sets by how much (1 turns it off), `pace.major_size` sets what counts as large.

### Step logs
- Optional `.msteps` files record what was done while modelling, for professional demonstrations. Pass one with `--steps` or drop it on the app. `mousestorm check-steps` validates a file. See `docs/STEPS_FORMAT.md`.

## 3.0.0

### Picture
- Six interface themes, each covering the workspace, the viewport and the end card.
- Film finishing on the final picture: colour grades, bloom, vignette, grain and optional letterboxing.
- An x-ray sweep through the turntable, from the finished render to clay and wireframe and back, aligned exactly because the same geometry drives both.
- The photographs now have real 3D parallax instead of a flat zoom.
- The turntable eases in and out of the hero pose.
- The camera drifts toward wherever the swarm is busiest.
- Soft glow under the mice.
- A swarm poster: a light-painting of every mouse's path, as a section of the finale and as a high-resolution PNG.

### Control
- One table of 70-odd options drives `--set`, TOML or JSON config files, the app's Advanced panel and `docs/OPTIONS.md`.
- Nine presets (`shorts`, `cinema`, `neon`, `blueprint`, `noir`, `paper`, `hacker`, `pure`, `quick`).
- Lighting rigs, backdrop and floor colours, lens, framing, swing, finale sections and their order, photo cameras, sample counts, phases that can be switched off, custom titles.
- Soundtrack moods, tempo, key, levels, a loudness target, or your own music file.
- H.265, quality, encoder effort, audio bitrate, GIF size.

### New
- `--storyboard`: six panels in seconds with no Cycles and no encoding, for trying options.
- Chapter markers and a title in MP4, MOV and WebM.
- `NAME_report.json` with the film's numbers.


## 2.0.0

### Picture
- The turntable is now synthesised with view interpolation from the scene's real geometry. Sparse Cycles frames no longer dissolve into each other, so the double image is gone.
- Portrait and square output, with a stacked workspace and camera framing of their own.
- Six pointer styles, a few named crew members, comet tails in place of screen-long lines.
- Motion with a hand behind it: overshoot and correction, tremor, and gestures that follow the tool.
- Opening title card; finale shots dissolve into each other; the end card keeps moving.
- The photo-shoot flash is about a third as bright. Nine full-white flashes in a row was a strobe.

### Sound
- A generated score that builds with the work and lands on the first frame of the finale.
- Clicks are voiced by what the operation is and panned to where the mouse is on screen.
- Mastered to −16 LUFS with a true-peak ceiling. It used to sit around −32 dB, with several seconds of digital silence in the turntable.

### Workflow
- `--duration` picks the speed for a target film length.
- `--aspect`, batch input (several files or a folder), `mousestorm doctor`, `--version`.
- Cycles shots are cached by content, so re-filming at another speed skips the slow part, and interrupted renders resume.
- The app gained frame shape, film length, soundtrack and a Stop button.

### Fixed
- A frame worker killed by the OS left the job hanging forever.
- ffmpeg failures now report ffmpeg's own message.
- Odd dimensions no longer crash the 4:2:0 encoders.
- An output extension that disagreed with the format produced a mislabelled file.
- Non-finite vertices poisoned every camera fit.
- Very small scenes fell behind a fixed near plane.
- The hero shot jumped in zoom as the turntable began.
- Mice aimed below the panel during the node-editor transition.
- The app now rejects cross-origin requests and malformed settings.

## 1.0.0

First release.
