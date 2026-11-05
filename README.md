mkboota compiles and repairs Android AOSP-compatible `bootanimation.zip` archives.

mkboota converts video files into compliant boot animations with interactive cropping
or multi-segment slicing, and sanitizes existing or corrupted archives that cause boot
failures, black screens, or texture exhaustion

## Technical Constraints

| Constraint       | Specification                   | Purpose                      |
| :--------------- | :------------------------------ | :--------------------------- |
| Compression      | `ZIP_STORED` (mode 0)           | Direct memory-mapped access  |
| Pitch Alignment  | Width divisible by 16           | GPU scanline buffer stride   |
| Dimension Parity | Even width and height           | Texture coordinate alignment |
| Line Endings     | LF with trailing newline        | Parser compatibility         |
| Texture Memory   | $\le$ 150 MB / $\le$ 300 frames | SurfaceFlinger memory limit  |
| Palette Format   | Indexed PNG-8 (256 colors)      | Reduced memory footprint     |

## Requirements

- Python >= 3.12
- FFmpeg and FFprobe in system `PATH`
- Dependencies: `pillow`, `PySide6`, `rich`

## Installation

Install via pip:

```bash
pip install .
```

To uninstall cleanly:

```bash
pip uninstall mkboota
```

## Usage

### GUI

```bash
# Launch desktop GUI studio
mkboota-gui

# Or launch via CLI subcommand
mkboota studio [input_file]
```

### CLI

#### Compilation

```bash
# Compile video with interactive cropping dialog
mkboota input.mp4

# Compile headless with center crop
mkboota input.mp4 --no-ui

# Compile with loop from beginning
mkboota input.mp4 --loop

# Compile with intro segment and looping remainder (e.g. loop at 2.5s)
mkboota input.mp4 --loop-start 2.5

# Multi-segment video slicing (part0, part1, etc.)
mkboota input.mp4 --parts "0-2.5s:c:1:30" "2.5-6s:p:0:0"

# Interactive wizard setup
mkboota input.mp4 --wizard

# Resource estimation without writing output file
mkboota input.mp4 --dry-run

# Compile with custom resolution and framerate
mkboota input.mp4 -o bootanimation.zip --resolution 1088x1920 --fps 30 --no-ui
```

#### Repair

```bash
# Repair an existing archive
mkboota repair broken_bootanimation.zip

# Repair with target VRAM limit (e.g. 120 MB)
mkboota repair broken.zip --max-vram 120

# Repair with loop frame decimation or hold-last conversion
mkboota repair broken.zip --decimate-loop --convert-hold-last

# Repair with explicit output path in quiet mode
mkboota repair broken.zip -o repaired.zip -q
```

#### Inspection and Verification

```bash
# Validate archive AOSP compliance
mkboota verify bootanimation.zip

# Inspect archive partitions and VRAM usage
mkboota inspect bootanimation.zip
```

### CLI Options

#### Compiler Options

| Option             | Default       | Description                                |
| :----------------- | :------------ | :----------------------------------------- |
| `video`            | _(required)_  | Input video file path                      |
| `-o, --output`     | `Dialog / -o` | Output archive path                        |
| `-q, --quiet`      | `false`       | Print only output path                     |
| `--loop`           | `false`       | Loop full animation                        |
| `--loop-start`     | `None`        | Start seconds for looping part             |
| `--loop-mode`      | `auto`        | Mode (`loop` or `hold-last`)               |
| `--parts`          | `None`        | Multi-segment specs (`0-2.5s:c:1:30`)      |
| `--no-ui`          | `false`       | Center crop without GUI                    |
| `--fps`            | `auto (≤30)`  | Framerate (e.g. 30, 60)                    |
| `-r, --resolution` | `960x2142`    | Dimensions `WxH` (width % 16)              |
| `--max-duration`   | `auto`        | Max extraction seconds                     |
| `--bg-color`       | `#000000`     | Background hex color `#RRGGBB`             |
| `--dry-run`        | `false`       | Estimate resources without writing archive |
| `--wizard`         | `false`       | Interactive parameter setup                |
| `--strict`         | `false`       | Enforce exact width without auto-alignment |

#### Multi-Segment Specification (`--parts`)

Segment syntax follows `<start>-<end>[s]:<type>:<count>:<pause>[:folder][:color][:clock]`:

- **`<type>`**: `c` plays to completion before switching;
  `p` is interruptible as soon as Android finishes booting
- **`<count>`**: `1` to play once (intro/exit), `0` to loop indefinitely,
  or `N` to repeat $N$ times
- **`<pause>`**: Number of frames to freeze on the last frame before proceeding or looping
- **Optional Positional Fields**:
  - `folder`: Archive directory name (defaults to `part0`, `part1`, ...)
  - `color`: Background hex color in `#RRGGBB` format for letterboxing (default: `#000000`)
  - `clock`: Y-axis coordinate in pixels to render system clock (default: `-1` / disabled)

#### Repair Options

| Option                    | Default       | Description                              |
| :------------------------ | :------------ | :--------------------------------------- |
| `input_zip`               | _(required)_  | Path to input archive                    |
| `-o, --output`            | `Dialog / -o` | Destination archive path                 |
| `-q, --quiet`             | `false`       | Print only output path                   |
| `--max-vram`              | `150.0`       | Target VRAM budget in MB                 |
| `--loop-fps`              | `None`        | Target framerate for looping partitions  |
| `--decimate-loop`         | `false`       | Decimate looping frames by 2x            |
| `--convert-hold-last`     | `false`       | Convert looping parts to 1-frame hold    |
| `-r, --target-resolution` | `None`        | Rescale canvas dimensions (`WxH`)        |
| `--strict`                | `false`       | Fail on alignment or validation warnings |
| `--dry-run`               | `false`       | Simulate repair without modifying files  |

## License

MIT. See [LICENSE](LICENSE)
