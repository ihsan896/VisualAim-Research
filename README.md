# VisualAim-Research

A color-detection aimbot, written to learn how real-time computer vision systems are actually built, measured and optimized.

The whole pipeline is here end to end: grab the screen, find targets by color, predict where they're going with a Kalman filter, turn that into mouse input. The point isn't to win games. It's to have a concrete system where every design decision has a measurable cost, and where getting it wrong is obvious.

---

## Read this first

**Don't run this in online competitive games.**

- It breaks the terms of service of every modern competitive shooter, Valorant (Riot Vanguard) and CS2 (VAC) included. Getting caught means a permanent ban on your account.
- Vanguard is a kernel-mode anti-cheat. This project doesn't try to get around it, can't, and isn't meant to.
- Beyond the account risk, cheating ruins the game for the people you're playing against.

Run it on your own machine, offline: the practice range, offline practice mode, a private server you host yourself. The detection and tracking layers also work on synthetic frames (plain colored rectangles), so you can study the algorithm without launching a game at all.

The author and contributors aren't responsible for what you do with this.

---

## What's in it

This repo is mostly about the gap between "a prototype that works" and "a system that works in real time."

| Topic | File |
|---|---|
| HSV thresholding, morphology, contour filtering | `core/detector.py` |
| Kalman filter (4 states: position + velocity) for motion prediction | `core/kalman_tracker.py` |
| Closed-loop control: correct a fraction of the error every frame | `core/aim_controller.py` |
| Sub-pixel relative mouse movement via Windows `SendInput` | `core/input_controller.py` |
| DXGI (dxcam) vs GDI (MSS) screen capture, a 130x difference | `core/capture.py` |
| Schema-driven config with type conversion and validation | `ui/config_manager.py` |
| Thread coordination and keeping the hot loop non-blocking | `main.py` |

---

## How it works

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ 1. Capture   │──▶│ 2. Detect    │──▶│ 3. Track     │──▶│ 4. Control   │
│              │   │              │   │              │   │              │
│ dxcam / MSS  │   │ BGR→HSV      │   │ Kalman       │   │ error × gain │
│ region or    │   │ inRange ×2   │   │ (x,y,vx,vy)  │   │ FOV gate     │
│ full screen  │   │ morphology   │   │ prediction + │   │ SendInput    │
│              │   │ contours     │   │ confidence   │   │ (relative)   │
└──────────────┘   └──────────────┘   └──────────────┘   └──────────────┘
      2.5 ms            2.0 ms            <0.1 ms            <0.1 ms
```

**Capture.** Either the whole screen or a box around the crosshair. dxcam uses DXGI Desktop Duplication, which is hardware accelerated. It returns `None` when the screen hasn't changed since the last grab. That's not an error, and treating it as one costs you frames: the fix is to reuse the last valid frame.

**Detect.** Convert to HSV, mask with two color ranges (red wraps around the hue circle, so you need two), clean up with morphology, then filter contours by area, aspect ratio and solidity. Whatever survives gets sorted by distance from the crosshair.

**Track.** The Kalman filter estimates position and velocity. When a target disappears behind smoke or a wall, prediction carries on and confidence decays each frame until it's no longer trusted.

**Control.** Compute the error vector from crosshair to target, throw it away if it's outside the FOV radius, otherwise move by `error × aim_speed`. Smoothing falls out of this for free: closing a fraction of the remaining distance every frame naturally decelerates as you approach. No easing layer needed.

---

## Numbers

All measured with the code in this repo, on an Intel Core i5-1135G7 with Iris Xe graphics.

### Screen capture, 1920×1080 full screen

| Backend | Frame time | Ceiling |
|---|---:|---:|
| MSS (GDI) | 52.2 ms | 19 FPS |
| **dxcam (DXGI)** | **0.4–2.5 ms** | **400+ FPS** |

GDI capture bit-blits the entire desktop every frame. DXGI reads the frame the GPU already produced.

### Detection cost on a 2560×1440 frame

| `detection_scale` | Time | Ceiling | Coordinate error |
|---|---:|---:|---:|
| 1.00 | 25.0 ms | 40 FPS | — |
| 0.50 | 7.8 ms | 128 FPS | 0 px |
| 0.33 | 3.5 ms | 289 FPS | 1 px |
| **0.25** | **2.0 ms** | **503 FPS** | **0 px** |

Detection cost scales with pixel count, so the frame is downscaled before processing and target coordinates are scaled back up afterwards. Downscaling uses `INTER_NEAREST`. `INTER_AREA` averages neighboring pixels, which blends the target color into the background and pushes it outside the HSV threshold. It's also about 10x slower.

### Detecting the game

| Method | Time |
|---|---:|
| `psutil.process_iter()`, walk every process | 37.4 ms |
| **Foreground window → PID → exe name** | **0.03 ms** |

37 ms per frame is a non-starter. So there are two layers: the exe name check runs four times a second, and the full process scan runs every two seconds, only to answer "is the game running at all."

### End to end

```
capture : 2.52 ms
detect  : 2.41 ms
────────────────
total   : 4.95 ms/frame  →  202 FPS
```

---

## Setup

Windows 10/11, Python 3.10 or newer (tested on 3.13).

```bash
git clone https://github.com/<user>/VisualAim-Research.git
cd VisualAim-Research
python -m pip install -r requirements.txt
```

| Package | Why |
|---|---|
| `opencv-python` | The image processing pipeline |
| `numpy` | Array work |
| `dxcam` + `comtypes` | Hardware accelerated capture |
| `mss` | Software capture, fallback |
| `pillow` | PIL capture, second fallback |
| `keyboard` | Global hotkeys |
| `psutil` | Game process detection (optional, falls back to window titles) |

---

## Running it

```bash
python main.py                # with the terminal menu
python main.py --no-menu      # hotkeys only
```

### Hotkeys

| Key | Action |
|---|---|
| `F2` | Toggle aim assist |
| `F3` | Toggle trigger |
| `F6` | Toggle recoil compensation |
| `F4` | Shut down cleanly |

### Terminal menu

```
[1] START BOT          [6] RECOIL CONTROL
[2] STOP BOT           [7] DISPLAY / DEBUG
[3] PERFORMANCE        [8] LOAD PROFILE
[4] AIMBOT SETTINGS    [9] SAVE PROFILE
[5] TRIGGERBOT         [l] VIEW LOGS
                       [0] EXIT
```

The status bar shows live FPS, latency, active mode and target state. The menu runs on its own thread so it never blocks the capture loop.

---

## Color calibration

Everything the system "knows" comes down to which color counts as a target. Get the range wrong and you either detect nothing or treat the entire screen as a target.

```bash
# Save a frame as PNG (for analysis or sharing)
python calibrate_hsv.py --snapshot 8 --backend dxcam

# Sample with global hotkeys, no window (works with fullscreen games)
python calibrate_hsv.py --live --apply --backend dxcam
#   F7 sample · F8 save · F9 reset · F10 quit

# Windowed mode, click to sample
python calibrate_hsv.py --apply
#   SPACE freeze · left click sample · M mask view · K save · Q quit
```

The tool detects hue wraparound on its own (red sits at both ends of the hue circle) and emits two ranges when it needs to. It also reports how consistent your samples were:

```
Sample color : red (~2°)  (median H=1 S=228 V=241)
Consistency  : H std=1.8  S std=12.4  V std=15.1
```

High deviation means you clicked on different things. Range bounds come from percentiles (p10/p90) rather than min/max, so one stray click doesn't blow the range wide open.

One note on the windowed mode: OpenCV only receives key presses while its own window has focus. If you launch from a terminal and never click the window, `Q` and `ESC` go nowhere. That's why `--live` exists.

---

## Configuration

Everything lives in `research_config.ini`. It's schema-driven: `ui/config_manager.py` defines the type, bounds and default for every key, clamps out-of-range values and falls back to the default on an invalid choice.

```ini
[capture]
backend = dxcam            ; dxcam | mss | pil | auto
full_screen = true         ; false uses the fov_x/fov_y box instead
detection_scale = 0.25     ; downscale before detection, matters on full screen
target_fps = 60

[color]
lower_h = 0                ; primary HSV range
upper_h = 10
lower_h2 = 170             ; secondary range, for hue wraparound
upper_h2 = 179
min_blob_size = 40         ; noise rejection
min_solidity = 0.5         ; rejects fragmented shapes

[aim]
mode = smooth              ; smooth | snap | hybrid
speed = 0.35               ; fraction of the error closed per frame
fov_radius = 250           ; targets outside this radius are ignored
head_offset = 0.28         ; fraction down from the top of the box

[game]
use_process_check = true   ; match by exe name, more reliable than window title
process_names = VALORANT.exe, cs2.exe
pause_when_closed = true   ; idle while the game isn't running

[profile]
auto_apply = false         ; true lets performance profiles overwrite your values
```

Leave `profile.auto_apply` on and the performance profile will overwrite `aim.speed`, `aim.smoothing`, `trigger.delay_*` and `capture.target_fps` on every launch, quietly undoing anything you tuned by hand.

Per-game settings (color ranges, weapon recoil patterns, aim preferences) live in `profiles/*.json` and load from `[8] LOAD PROFILE`.

---

## Layout

```
VisualAim-Research/
├── core/
│   ├── capture.py          # screen capture (dxcam/MSS/PIL), region support
│   ├── detector.py         # HSV thresholding, morphology, contour filtering
│   ├── kalman_tracker.py   # 4-state Kalman filter
│   ├── aim_controller.py   # FOV gating, movement calculation
│   ├── input_controller.py # SendInput, sub-pixel accumulation
│   ├── trigger.py          # trigger logic and mode handling
│   └── recoil.py           # per-weapon recoil patterns
├── modules/
│   ├── anti_ban.py         # human-like delays and jitter
│   ├── hotkey_manager.py   # global hotkeys
│   └── performance_profiles.py
├── ui/
│   ├── config_manager.py   # schema-driven INI/JSON config
│   ├── logger.py           # colored logging + metrics collection
│   └── menu.py             # terminal interface
├── profiles/               # per-game JSON profiles
├── main.py                 # main loop and coordination
├── calibrate_hsv.py        # color calibration tool
└── research_config.ini
```

About 9,400 lines of Python.

---

## Things that turned out to matter

A few observations from building this that generalize past the specific project.

**Non-blocking beats fast.** Capture came down to 2.5 ms and the loop still sat at 18 FPS. The mouse movement function had a `time.sleep(50ms)` inside it, and it ran on every single frame a target was visible. One sleep in the wrong place cancels every other optimization you've done.

**A closed loop already smooths itself.** A controller that closes 35% of the error each frame doesn't need an easing curve on top. The extra layer only adds latency.

**An empty frame isn't always a failure.** DXGI returns nothing when the screen content hasn't changed. Code that treated this as an error was dropping frames and losing track of targets. The correct response is to reuse the last frame.

**Profile before you optimize.** The system was slow, and the assumed cause was weak integrated graphics. Measurement showed 97% of the frame time was going into the capture backend. Changing one config line took it from 6.9 FPS to 202 FPS.

**A setting nobody reads isn't a setting.** Dozens of keys in the config file were never read by anything. You'd change a value, believe you'd changed the behavior, and the system would keep running on a hardcoded constant. Related: `configparser` treats inline comments as part of the value by default, so `head_offset = 0.28  # head level` silently falls back to the default.

---

## Tests

These run on synthetic frames. No game, no real mouse movement.

```bash
python ui/config_manager.py        # config layer self-test
python modules/performance_profiles.py
python test_valorant_profile.py    # profile file validation
```

---

## Contributing

This is a learning project. Bug reports and improvements are welcome. Contributions aimed at evading anti-cheat systems are not.

---

## References

- [OpenCV: color spaces and thresholding](https://docs.opencv.org/4.x/df/d9d/tutorial_py_colorspaces.html)
- [OpenCV: morphological transformations](https://docs.opencv.org/4.x/d9/d61/tutorial_py_morphological_ops.html)
- [How a Kalman filter works, in pictures](https://www.bzarg.com/p/how-a-kalman-filter-works-in-pictures/)
- [DXGI Desktop Duplication API](https://learn.microsoft.com/en-us/windows/win32/direct3ddxgi/desktop-dup-api)
- [Windows SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)
