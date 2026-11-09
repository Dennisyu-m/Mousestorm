# Advanced options

Generated from `mousestorm/options.py`. Set them with `--set key=value`, in a config file (`--config film.toml`) or in the app's Advanced panel.

```toml
# film.toml
[film]
speed = 3
aspect = "9:16"

[look]
theme = "neon"
bloom = 0.6

[camera]
lens = 85
```

## Presets

| Name | Result |
| --- | --- |
| `shorts` | 30-second vertical film |
| `cinema` | Widescreen, graded, unhurried |
| `neon` | Magenta and cyan at night |
| `blueprint` | White lines on engineering blue |
| `noir` | Hard light, black and white |
| `paper` | A bright, quiet studio |
| `hacker` | Green on black |
| `pure` | No interface, just the model and the mice |
| `quick` | Fast and rough, for checking settings |

## Look

| Option | Default | Allowed | Meaning |
| --- | --- | --- | --- |
| `look.theme` | `graphite` | `graphite`, `midnight`, `paper`, `neon`, `terminal`, `blueprint` | Interface theme |
| `look.grade` | `clean` | `none`, `clean`, `warm`, `cool`, `teal_orange`, `noir`, `vivid`, `film` | Colour grade |
| `look.saturation` | `1.0` | 0 to 2 | Saturation |
| `look.contrast` | `1.0` | 0 to 2 | Contrast |
| `look.vignette` | `0.22` | 0 to 1 | Vignette |
| `look.grain` | `0.1` | 0 to 1 | Film grain |
| `look.bloom` | `0.3` | 0 to 1 | Bloom |
| `look.letterbox` | `off` | `off`, `1.85`, `2.00`, `2.39` | Letterbox in the finale |
| `look.exposure` | `0.0` | -3 to 3 | Exposure (stops) |
| `look.view_transform` | `agx` | `agx`, `filmic`, `standard` | View transform |
| `look.lights` | `studio` | `studio`, `sunset`, `noir`, `clean`, `neon` | Lighting rig |
| `look.light_power` | `1.0` | 0.2 to 3 | Light power |
| `look.floor` | `auto` | `auto`, `on`, `off` | Studio floor |
| `look.floor_color` | theme / automatic | hex colour | Floor colour |
| `look.backdrop_top` | theme / automatic | hex colour | Backdrop top |
| `look.backdrop_bottom` | theme / automatic | hex colour | Backdrop bottom |

## Mice

| Option | Default | Allowed | Meaning |
| --- | --- | --- | --- |
| `swarm.cursor_scale` | `1.0` | 0.5 to 3 | Cursor size |
| `swarm.trail` | `150.0` | 0 to 500 | Trail length |
| `swarm.glow` | `0.35` | 0 to 1 | Cursor glow |
| `swarm.ripples` | `True` | true, false | Click ripples |
| `swarm.chips` | `True` | true, false | Parameter pop-ups |
| `swarm.humanness` | `1.0` | 0 to 2 | Hand-like motion |
| `swarm.fidget` | `True` | true, false | Idle mice fidget |
| `swarm.skins` | `classic,dart,pixel,hollow,bubble,critter` | `classic`, `dart`, `pixel`, `hollow`, `bubble`, `critter` | Pointer styles |
| `swarm.palette` | theme / automatic | hex colours, comma separated | Mouse colours |
| `swarm.names` | `-1` | -1 to 64 | Name tags (-1 = auto) |
| `swarm.crew` | theme / automatic | text | Custom names, comma separated |

## Pacing

| Option | Default | Allowed | Meaning |
| --- | --- | --- | --- |
| `pace.shading` | `True` | true, false | Show the shading phase |
| `pace.lighting` | `True` | true, false | Show the lighting phase |
| `pace.render` | `True` | true, false | Show the render tiles |
| `pace.model_weight` | `1.0` | 0.2 to 4 | Modelling length |
| `pace.shade_weight` | `1.0` | 0.2 to 4 | Shading length |
| `pace.tweak_speed` | `6.0` | 1 to 20 | Extra speed for small adjustments (1 = off) |
| `pace.major_size` | `0.08` | 0.005 to 0.5 | Change that counts as major (share of the model) |

## Workspace

| Option | Default | Allowed | Meaning |
| --- | --- | --- | --- |
| `ui.layout` | `full` | `full`, `focus`, `clean` | Workspace layout |
| `ui.telemetry` | `True` | true, false | Live counters |

## Camera

| Option | Default | Allowed | Meaning |
| --- | --- | --- | --- |
| `camera.azimuth` | `-58.0` | -180 to 180 | Hero azimuth |
| `camera.elevation` | `15.0` | 0 to 80 | Hero elevation |
| `camera.use_scene` | `True` | true, false | Start from the file's own camera angle |
| `camera.swing` | `74.0` | 0 to 360 | Swing during the build |
| `camera.follow` | `0.3` | 0 to 1 | Follow the action |
| `camera.lens` | `50.0` | 18 to 135 | Lens (mm) |
| `camera.margin` | `0.09` | 0 to 0.5 | Frame margin |

## Finale

| Option | Default | Allowed | Meaning |
| --- | --- | --- | --- |
| `show.segments` | `hero,turntable,shoot,poster,sheet` | `hero`, `turntable`, `shoot`, `poster`, `sheet` | Finale sections, in order |
| `show.turns` | `1` | 1 to 4 | Turntable revolutions |
| `show.direction` | `ccw` | `ccw`, `cw` | Turntable direction |
| `show.turn_frames` | `0` | 0 to 720 | Cycles frames in the turntable (0 = by quality) |
| `show.wipe` | `True` | true, false | X-ray sweep |
| `show.parallax` | `0.5` | 0 to 1 | Parallax on photos |
| `show.flash` | `0.3` | 0 to 1 | Camera flash |
| `show.cameras` | `front,right,back,high,top,low,detail1,detail2,scene` | `front`, `right`, `back`, `high`, `top`, `low`, `detail1`, `detail2`, `scene` | Photo cameras |

## Titles

| Option | Default | Allowed | Meaning |
| --- | --- | --- | --- |
| `title.show` | `True` | true, false | Opening title |
| `title.text` | theme / automatic | text | Title (default: file name) |
| `title.subtitle` | theme / automatic | text | Subtitle (default: statistics) |
| `title.caption` | `True` | true, false | Caption on the hero shot |
| `title.footer` | theme / automatic | text | End-card footer |
| `title.branding` | `True` | true, false | Mousestorm mark on the end card |

## Cycles

| Option | Default | Allowed | Meaning |
| --- | --- | --- | --- |
| `render.samples` | `0` | 0 to 8192 | Photo samples (0 = by quality) |
| `render.turn_samples` | `0` | 0 to 8192 | Turntable samples (0 = by quality) |
| `render.denoise` | `True` | true, false | Denoise |
| `render.bounces` | `8` | 1 to 32 | Light bounces |

## Sound

| Option | Default | Allowed | Meaning |
| --- | --- | --- | --- |
| `sound.mood` | `dusk` | `dusk`, `bright`, `drive`, `calm` | Mood |
| `sound.tempo` | `100.0` | 60 to 180 | Tempo (BPM) |
| `sound.transpose` | `0` | -12 to 12 | Transpose (semitones) |
| `sound.music` | `1.0` | 0 to 2 | Music level |
| `sound.clicks` | `1.0` | 0 to 2 | Click level |
| `sound.loudness` | `-16.0` | -30 to -9 | Loudness (LUFS) |
| `sound.track` | theme / automatic | text | Your own music file (replaces the score) |

## Encoding

| Option | Default | Allowed | Meaning |
| --- | --- | --- | --- |
| `encode.codec` | `h264` | `h264`, `h265` | MP4 codec |
| `encode.crf` | `0` | 0 to 51 | Constant quality (0 = by quality) |
| `encode.preset` | `medium` | `ultrafast`, `veryfast`, `fast`, `medium`, `slow`, `veryslow` | Encoder effort |
| `encode.audio_kbps` | `192` | 64 to 512 | Audio bitrate (kbps) |
| `encode.gif_width` | `960` | 160 to 1920 | GIF long side |
| `encode.gif_fps` | `24` | 5 to 50 | GIF frame rate |
| `encode.chapters` | `True` | true, false | Chapter markers |

## Extra files

| Option | Default | Allowed | Meaning |
| --- | --- | --- | --- |
| `output.poster` | `True` | true, false | Swarm poster |
| `output.stills` | `True` | true, false | Photos |
| `output.sheet` | `True` | true, false | Contact sheet |
| `output.report` | `True` | true, false | JSON report |
