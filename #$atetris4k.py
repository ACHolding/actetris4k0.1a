#!/usr/bin/env python3
"""ac's. tetris 0.1 — Famicom rules @ 60 FPS, retro in-game UI. Python 3.14+. FILES=OFF."""
from __future__ import annotations

import array
import math
import os
import sys
import random
import site

# macOS: prefer pygame's bundled SDL2 (avoids duplicate SDL objc warnings with Homebrew)
if sys.platform == "darwin":
    for _sp in site.getsitepackages():
        _dylibs = os.path.join(_sp, "pygame", ".dylibs")
        if os.path.isdir(_dylibs):
            _prev = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (
                _dylibs + (os.pathsep + _prev if _prev else "")
            )
            break

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame

FILES_OFF = True  # No .wav / .ogg / ROM files on disk — audio is synthesized in-code.
APP_TITLE = "ac's. tetris 0.1"
MENU_TITLE_LINES = ("TETRIS",)  # NES-style banded logo, centered on title screen

# --- Audio (init before pygame.init for stable mixer on 3.14) ---
_SAMPLE_RATE = 22050
_GB_BPM = 150  # Game Boy Tetris Type A (Korobeiniki) — VGMPF / retail soundtrack
_TICK_SEC = 60.0 / _GB_BPM / 4.0  # 16th-note length at 150 BPM (~0.1s)

pygame.mixer.pre_init(_SAMPLE_RATE, -16, 1, 512)
pygame.init()
pygame.font.init()

# Note names → MIDI (Game Boy Type A is in A minor; lead in upper register)
_NAME_MIDI = {
    "E3": 52, "G#3": 56, "A3": 57, "B3": 59, "C4": 60, "D4": 62, "E4": 64,
    "F4": 65, "G4": 67, "A4": 69, "B4": 71, "C5": 72, "D5": 74, "E5": 76,
    "F5": 77, "G5": 79, "A5": 81, "B5": 83, "P": 0,
}


def _eighths_to_ticks(events: list[tuple[str, float]]) -> list[tuple[int, int]]:
    """Convert (note, length in eighth notes) → (midi, 16th ticks)."""
    out: list[tuple[int, int]] = []
    for name, eighths in events:
        mid = _NAME_MIDI.get(name, 0)
        ticks = max(1, int(round(eighths * 2)))  # 2 sixteenths per eighth
        out.append((mid, ticks))
    return out


# Korobeiniki Type A — main phrases (Game Boy / YouTube retail loop, 150 BPM)
_GB_PHRASE = [
    ("E5", 1), ("B4", 1), ("C5", 1), ("D5", 1), ("E5", 1), ("D5", 1), ("C5", 1), ("B4", 1),
    ("A4", 1), ("A4", 1), ("C5", 1), ("E5", 1), ("E5", 1), ("D5", 1), ("C5", 1), ("B4", 2),
    ("C5", 1), ("D5", 1), ("E5", 1), ("C5", 1), ("A4", 1), ("A4", 1), ("A4", 1), ("B4", 1),
    ("C5", 1), ("D5", 1), ("E5", 1), ("D5", 1), ("C5", 1), ("B4", 1), ("A4", 2), ("A4", 2),
    ("E5", 1), ("E5", 1), ("E5", 1), ("C5", 1), ("D5", 1), ("E5", 1), ("D5", 1), ("C5", 1),
    ("B4", 1), ("B4", 1), ("C5", 1), ("D5", 1), ("E5", 1), ("C5", 1), ("A4", 1), ("A4", 2),
]

# Bridge + ending (low run + pickup — matches full YouTube Type A loop tail)
_GB_BRIDGE = [
    ("E5", 2), ("C5", 2), ("D5", 2), ("B4", 2), ("C5", 2), ("A4", 2), ("G#3", 2), ("E3", 2),
    ("E4", 2), ("C4", 2), ("D4", 2), ("B3", 2), ("C4", 2), ("E4", 2), ("A4", 2), ("G#4", 2),
    ("E5", 2), ("B4", 1), ("C5", 1), ("D5", 1), ("E5", 1), ("D5", 1), ("C5", 1), ("B4", 1),
    ("A4", 1), ("A4", 1), ("C5", 1), ("E5", 1), ("D5", 1), ("C5", 1), ("B4", 2),
    ("C5", 1), ("D5", 1), ("E5", 1), ("C5", 1), ("A4", 2), ("P", 2),
]

_THEME_MELODY = _eighths_to_ticks(_GB_PHRASE * 2 + _GB_BRIDGE)


# Game Boy pulse-2 harmony: parallel minor third below melody (DMG-style counter line)
def _harmony_track(melody: list[tuple[int, int]], interval: int = -3) -> list[tuple[int, int]]:
    return [(0 if mid == 0 else max(40, mid + interval), dur) for mid, dur in melody]


_THEME_HARMONY = _harmony_track(_THEME_MELODY, -3)

# Pulse-2 bass roots (approx. retail loop — square low, not NES triangle)
_THEME_BASS = [
    (40, 8), (47, 8), (40, 8), (47, 8), (45, 8), (52, 8), (45, 8), (52, 8),
    (40, 8), (47, 8), (40, 8), (47, 8), (45, 8), (52, 8), (45, 8), (52, 8),
    (43, 8), (50, 8), (43, 8), (50, 8), (40, 8), (47, 8), (40, 8), (47, 8),
    (45, 8), (52, 8), (45, 8), (52, 8), (40, 8), (47, 8), (40, 8), (47, 8),
    (38, 8), (45, 8), (38, 8), (45, 8), (40, 8), (47, 8), (40, 16),
]


def _midi_hz(mid: int) -> float:
    if mid == 0:
        return 0.0
    return 440.0 * (2.0 ** ((mid - 69) / 12.0))


def _synth_track(
    track: list[tuple[int, int]],
    wave_type: str,
    *,
    duty: float = 0.25,
    peak: float = 1.0,
) -> list[float]:
    """DMG-style squares: 12.5% / 25% / 50% duty; soft note envelope."""
    samples: list[float] = []
    phase = 0.0
    for note, dur in track:
        n = int(dur * _TICK_SEC * _SAMPLE_RATE)
        freq = _midi_hz(note)
        if freq <= 0.0 or n <= 0:
            samples.extend([0.0] * max(0, n))
            continue
        step = freq / _SAMPLE_RATE
        for i in range(n):
            phase = (phase + step) % 1.0
            if wave_type == "pulse":
                raw = 1.0 if phase < duty else -1.0
            else:
                p = phase
                raw = 4.0 * p if p < 0.25 else (2.0 - 4.0 * p if p < 0.75 else 4.0 * p - 4.0)
            t = i / max(1, n - 1)
            attack = min(1.0, i / 80.0)
            release = (1.0 - t) ** (1.35 if wave_type == "pulse" else 0.55)
            samples.append(raw * attack * release * peak)
    return samples


def _build_korobeiniki_sound() -> pygame.mixer.Sound:
    lead = _synth_track(_THEME_MELODY, "pulse", duty=0.25, peak=0.55)
    harm = _synth_track(_THEME_HARMONY, "pulse", duty=0.50, peak=0.22)
    bass = _synth_track(_THEME_BASS, "pulse", duty=0.50, peak=0.30)
    length = len(lead)
    if length == 0:
        length = max(len(harm), len(bass), 1)
    mixed = array.array("h")
    for idx in range(length):
        m = lead[idx] if idx < len(lead) else 0.0
        h = harm[idx % len(harm)] if harm else 0.0
        b = bass[idx % len(bass)] if bass else 0.0
        val = int(m * 5200.0 + h * 3200.0 + b * 3800.0)
        mixed.append(max(-32768, min(32767, val)))
    return pygame.mixer.Sound(buffer=mixed)


class GameSettings:
    """Runtime options (FILES=OFF — nothing written to disk)."""

    def __init__(self) -> None:
        self.music_enabled = True
        self.sfx_enabled = True
        self.music_volume = 0.45
        self.sfx_volume = 0.55


_SETTINGS = GameSettings()


def _synth_blip(
    midi: int,
    frames: int,
    *,
    duty: float = 0.25,
    peak: float = 0.4,
) -> pygame.mixer.Sound | None:
    freq = _midi_hz(midi)
    if freq <= 0 or frames <= 0:
        return None
    buf = array.array("h")
    phase = 0.0
    step = freq / _SAMPLE_RATE
    for i in range(frames):
        phase = (phase + step) % 1.0
        raw = 1.0 if phase < duty else -1.0
        env = min(1.0, i / 40.0) * (1.0 - i / max(1, frames - 1)) ** 0.8
        val = int(raw * env * peak * 12000.0)
        buf.append(max(-32768, min(32767, val)))
    try:
        return pygame.mixer.Sound(buffer=buf)
    except pygame.error:
        return None


class TetrisAudio:
    """Procedural music + menu/game SFX (no .png / .wav files)."""

    def __init__(self) -> None:
        self._enabled = False
        self._music: pygame.mixer.Sound | None = None
        self._playing = False
        self._sfx_move: pygame.mixer.Sound | None = None
        self._sfx_select: pygame.mixer.Sound | None = None
        self._sfx_cancel: pygame.mixer.Sound | None = None
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(_SAMPLE_RATE, -16, 1, 512)
            self._music = _build_korobeiniki_sound()
            self._sfx_move = _synth_blip(76, int(0.04 * _SAMPLE_RATE))
            self._sfx_select = _synth_blip(84, int(0.07 * _SAMPLE_RATE))
            self._sfx_cancel = _synth_blip(60, int(0.09 * _SAMPLE_RATE), duty=0.5)
            self._enabled = True
        except (pygame.error, ValueError, OSError):
            self._enabled = False

    def _play_sfx(self, snd: pygame.mixer.Sound | None) -> None:
        if not self._enabled or not _SETTINGS.sfx_enabled or snd is None:
            return
        try:
            snd.set_volume(_SETTINGS.sfx_volume)
            snd.play()
        except pygame.error:
            pass

    def menu_move(self) -> None:
        self._play_sfx(self._sfx_move)

    def menu_select(self) -> None:
        self._play_sfx(self._sfx_select)

    def menu_cancel(self) -> None:
        self._play_sfx(self._sfx_cancel)

    def play_game_music(self) -> None:
        if (
            not self._enabled
            or not _SETTINGS.music_enabled
            or self._music is None
            or self._playing
        ):
            return
        try:
            self._music.set_volume(_SETTINGS.music_volume)
            self._music.play(loops=-1)
            self._playing = True
        except pygame.error:
            pass

    def stop(self) -> None:
        if not self._enabled:
            return
        try:
            if pygame.mixer.get_init():
                pygame.mixer.stop()
        except pygame.error:
            pass
        self._playing = False


_GAME_AUDIO = TetrisAudio()

# --- Display / timing (NTSC Famicom @ 60 FPS; one loop tick = one frame) ---
# Retail NTSC hardware runs ~60.098 Hz; pygame caps at 60 for practical parity.
FAMICOM_FPS = 60
FPS = FAMICOM_FPS
FAMICOM_ARE_FRAMES = 10          # entry delay before new piece accepts input
FAMICOM_LINE_CLEAR_FRAMES = 20   # freeze after lock when lines will clear

# In-game layout (FILES=OFF — procedural UI, no image assets)
SCREEN_WIDTH = 640
SCREEN_HEIGHT = 480
BLOCK_SIZE = 20
GRID_WIDTH = 10
GRID_HEIGHT = 20
PLAY_W = GRID_WIDTH * BLOCK_SIZE
PLAY_H = GRID_HEIGHT * BLOCK_SIZE
PLAY_X = 100
PLAY_Y = 72
SIDEBAR_X = PLAY_X + PLAY_W + 28

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GRAY = (50, 50, 50)
LIGHT_GRAY = (150, 150, 150)
WELL_BG = (12, 16, 40)
WELL_GRID = (48, 72, 140)
FRAME_GRAY = (128, 128, 128)
FRAME_CYAN = (72, 200, 255)
LABEL_YELLOW = (255, 216, 0)
TITLE_ORANGE = (255, 128, 0)
ARROW_ORANGE = (255, 160, 40)

PIECE_COLORS: dict[str, tuple[int, int, int]] = {
    "I": (0, 240, 240),
    "O": (255, 255, 64),
    "T": (255, 48, 48),
    "S": (64, 220, 64),
    "Z": (180, 64, 255),
    "J": (64, 96, 255),
    "L": (255, 140, 32),
}
_PREVIEW_CELLS: dict[str, tuple[tuple[int, int], ...]] = {
    "T": ((1, 0), (0, 1), (1, 1), (2, 1)),
    "J": ((0, 0), (0, 1), (0, 2), (1, 2)),
    "Z": ((0, 0), (1, 0), (1, 1), (2, 1)),
    "O": ((0, 0), (1, 0), (0, 1), (1, 1)),
    "S": ((1, 0), (2, 0), (0, 1), (1, 1)),
    "L": ((2, 0), (0, 1), (1, 1), (2, 1)),
    "I": ((0, 1), (1, 1), (2, 1), (3, 1)),
}

_HIGH_SCORE = 0

# Four rotation states per piece (Famicom / Game Boy SRS-lite)
SHAPES = {
    "I": [
        [[0, 0, 0, 0], [1, 1, 1, 1], [0, 0, 0, 0], [0, 0, 0, 0]],
        [[0, 0, 1, 0], [0, 0, 1, 0], [0, 0, 1, 0], [0, 0, 1, 0]],
        [[0, 0, 0, 0], [0, 0, 0, 0], [1, 1, 1, 1], [0, 0, 0, 0]],
        [[0, 1, 0, 0], [0, 1, 0, 0], [0, 1, 0, 0], [0, 1, 0, 0]],
    ],
    "O": [[[1, 1], [1, 1]]],
    "T": [
        [[0, 1, 0], [1, 1, 1], [0, 0, 0]],
        [[0, 1, 0], [0, 1, 1], [0, 1, 0]],
        [[0, 0, 0], [1, 1, 1], [0, 1, 0]],
        [[0, 1, 0], [1, 1, 0], [0, 1, 0]],
    ],
    "S": [
        [[0, 1, 1], [1, 1, 0], [0, 0, 0]],
        [[0, 1, 0], [0, 1, 1], [0, 0, 1]],
        [[0, 0, 0], [0, 1, 1], [1, 1, 0]],
        [[1, 0, 0], [1, 1, 0], [0, 1, 0]],
    ],
    "Z": [
        [[1, 1, 0], [0, 1, 1], [0, 0, 0]],
        [[0, 0, 1], [0, 1, 1], [0, 1, 0]],
        [[0, 0, 0], [1, 1, 0], [0, 1, 1]],
        [[0, 1, 0], [1, 1, 0], [1, 0, 0]],
    ],
    "J": [
        [[1, 0, 0], [1, 1, 1], [0, 0, 0]],
        [[0, 1, 1], [0, 1, 0], [0, 1, 0]],
        [[0, 0, 0], [1, 1, 1], [0, 0, 1]],
        [[0, 1, 0], [0, 1, 0], [1, 1, 0]],
    ],
    "L": [
        [[0, 0, 1], [1, 1, 1], [0, 0, 0]],
        [[0, 1, 0], [0, 1, 0], [0, 1, 1]],
        [[0, 0, 0], [1, 1, 1], [1, 0, 0]],
        [[1, 1, 0], [0, 1, 0], [0, 1, 0]],
    ],
}
SHAPE_KEYS = list(SHAPES.keys())
COLORS = [BLACK] + [PIECE_COLORS[k] for k in SHAPE_KEYS]

# NTSC Famicom / NES Tetris — gravity period in frames @ 60 Hz (official table)
FAMICOM_GRAVITY_FRAMES: dict[int, int] = {
    0: 48, 1: 43, 2: 38, 3: 33, 4: 28, 5: 23, 6: 18, 7: 13, 8: 8, 9: 6,
    10: 5, 11: 5, 12: 5, 13: 4, 14: 4, 15: 4, 16: 3, 17: 3, 18: 3, 19: 2,
}
# Levels 20–28: 2 frames; 29+: 1 frame (max speed)
FAMICOM_DAS_DELAY = 16   # frames before auto-shift (DAS idle during ARE / line clear)
FAMICOM_DAS_REPEAT = 6   # frames between auto-shifts


def famicom_are_frames(piece: "Piece") -> int:
    """NES base ARE (10) + height bonus; simplified without full DOR table."""
    entry_rows = sum(1 for row in piece.matrix if any(row))
    return FAMICOM_ARE_FRAMES + 2 * max(0, entry_rows - 1)


def famicom_gravity_frames(level: int) -> int:
    lv = min(max(level, 0), 29)
    if lv >= 29:
        return 1
    if lv >= 20:
        return 2
    return FAMICOM_GRAVITY_FRAMES.get(lv, 2)


# --- In-play UI (game engine only; main menu unchanged) ---
_RECT_PLAY_FRAME = pygame.Rect(PLAY_X - 8, PLAY_Y - 8, PLAY_W + 16, PLAY_H + 16)
_RECT_NEXT_BOX = pygame.Rect(SIDEBAR_X, 300, 88, 88)


def _game_font(size: int = 14) -> pygame.font.Font:
    return pygame.font.SysFont("Courier New", size, bold=True)


def _draw_frame_box(screen: pygame.Surface, rect: pygame.Rect) -> None:
    """Thick grey border with cyan inner stroke (playfield / NEXT box)."""
    pygame.draw.rect(screen, FRAME_GRAY, rect, 6)
    inner = rect.inflate(-6, -6)
    pygame.draw.rect(screen, FRAME_CYAN, inner, 2)
    pygame.draw.rect(screen, BLACK, inner.inflate(-2, -2))


def _draw_well_grid(screen: pygame.Surface) -> None:
    for r in range(GRID_HEIGHT):
        for c in range(GRID_WIDTH):
            x = PLAY_X + c * BLOCK_SIZE
            y = PLAY_Y + r * BLOCK_SIZE
            pygame.draw.rect(screen, WELL_BG, (x, y, BLOCK_SIZE, BLOCK_SIZE))
            pygame.draw.rect(screen, WELL_GRID, (x, y, BLOCK_SIZE, BLOCK_SIZE), 1)


def _draw_title(screen: pygame.Surface, font: pygame.font.Font) -> None:
    title = font.render("TETRIS", True, TITLE_ORANGE)
    tm = _game_font(10).render("TM", True, TITLE_ORANGE)
    cx = PLAY_X + PLAY_W // 2
    tx = cx - title.get_width() // 2
    ty = 14
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
        shadow = font.render("TETRIS", True, BLACK)
        screen.blit(shadow, (tx + dx, ty + dy))
    screen.blit(title, (tx, ty))
    screen.blit(tm, (tx + title.get_width() + 2, ty - 2))


def _draw_drop_arrow(
    screen: pygame.Surface,
    piece: "Piece",
) -> None:
    cells = [(piece.x + c, piece.y + r) for r, row in enumerate(piece.matrix) for c, v in enumerate(row) if v]
    if not cells:
        return
    min_c = min(c for c, _ in cells)
    max_c = max(c for c, _ in cells)
    top_r = min(r for _, r in cells)
    ax = PLAY_X + (min_c + max_c + 1) * BLOCK_SIZE // 2
    ay = PLAY_Y + top_r * BLOCK_SIZE - 10
    pygame.draw.polygon(
        screen,
        ARROW_ORANGE,
        [(ax, ay), (ax - 7, ay - 12), (ax + 7, ay - 12)],
    )


def _draw_beveled_block(
    screen: pygame.Surface,
    x: int,
    y: int,
    color: tuple[int, int, int],
    size: int = BLOCK_SIZE,
) -> None:
    pygame.draw.rect(screen, color, (x, y, size, size))
    hi = tuple(min(255, c + 72) for c in color)
    lo = tuple(max(0, c - 72) for c in color)
    pygame.draw.line(screen, hi, (x, y), (x + size - 1, y))
    pygame.draw.line(screen, hi, (x, y), (x, y + size - 1))
    pygame.draw.line(screen, lo, (x + size - 1, y), (x + size - 1, y + size - 1))
    pygame.draw.line(screen, lo, (x, y + size - 1), (x + size - 1, y + size - 1))


def _draw_preview_piece(
    screen: pygame.Surface,
    shape_key: str,
    center: tuple[int, int],
    cell: int = 14,
) -> None:
    cells = _PREVIEW_CELLS[shape_key]
    min_x = min(c for c, _ in cells)
    min_y = min(r for _, r in cells)
    max_x = max(c for c, _ in cells)
    max_y = max(r for _, r in cells)
    pw = (max_x - min_x + 1) * cell
    ph = (max_y - min_y + 1) * cell
    ox = center[0] - pw // 2
    oy = center[1] - ph // 2
    color = PIECE_COLORS[shape_key]
    for cx, cy in cells:
        _draw_beveled_block(screen, ox + (cx - min_x) * cell, oy + (cy - min_y) * cell, color, cell)


def _color_for_grid(val: int) -> tuple[int, int, int] | None:
    if val <= 0:
        return None
    return COLORS[val]


class Piece:
    def __init__(self, shape_key: str):
        self.type = shape_key
        self.rotations = SHAPES[shape_key]
        self.rotation_idx = 0
        self.color_index = SHAPE_KEYS.index(shape_key) + 1
        self.matrix = self.rotations[0]
        self.x = 4 - len(self.matrix[0]) // 2
        self.y = 0

    def rotate(self, clockwise: bool = True) -> list[list[int]]:
        if len(self.rotations) <= 1:
            return self.matrix
        step = 1 if clockwise else -1
        self.rotation_idx = (self.rotation_idx + step) % len(self.rotations)
        self.matrix = self.rotations[self.rotation_idx]
        return self.matrix


class TetrisGame:
    def __init__(self, screen: pygame.Surface):
        self.screen = screen
        self.clock = pygame.time.Clock()
        self.font = _game_font(14)
        self.large_font = _game_font(32)
        self.title_font = _game_font(42)
        self.label_font = _game_font(16)
        self.value_font = _game_font(22)
        self.reset_game()

    def reset_game(self) -> None:
        self.grid = [[0] * GRID_WIDTH for _ in range(GRID_HEIGHT)]
        self.score = 0
        self.lines_cleared = 0
        self.level = 0
        self.game_over = False
        self.current_piece = self.get_new_piece()
        self.next_piece = self.get_new_piece()
        self.drop_timer = 0
        self.das_counter = 0
        self.das_dir = 0
        self.freeze_frames = 0
        self.freeze_reason: str | None = None
        self.pending_lines: list[int] = []
        self._start_are()

    def _input_frozen(self) -> bool:
        return self.freeze_frames > 0 and not self.game_over

    def _start_are(self) -> None:
        self.freeze_frames = famicom_are_frames(self.current_piece)
        self.freeze_reason = "are"
        self.drop_timer = 0
        self.das_counter = 0
        self.das_dir = 0

    def _spawn_next_after_lock(self) -> None:
        self.current_piece = self.next_piece
        self.next_piece = self.get_new_piece()
        if self.check_collision(self.current_piece):
            self.game_over = True
            self._update_high_score()
            return
        self._start_are()

    def _update_high_score(self) -> None:
        global _HIGH_SCORE
        if self.score > _HIGH_SCORE:
            _HIGH_SCORE = self.score

    def get_new_piece(self) -> Piece:
        return Piece(random.choice(SHAPE_KEYS))

    def check_collision(
        self,
        piece: Piece,
        offset_x: int = 0,
        offset_y: int = 0,
        shape: list[list[int]] | None = None,
    ) -> bool:
        target_shape = shape if shape is not None else piece.matrix
        for r, row in enumerate(target_shape):
            for c, val in enumerate(row):
                if val:
                    nx = piece.x + c + offset_x
                    ny = piece.y + r + offset_y
                    if nx < 0 or nx >= GRID_WIDTH or ny >= GRID_HEIGHT:
                        return True
                    if ny >= 0 and self.grid[ny][nx]:
                        return True
        return False

    def try_rotate(self, clockwise: bool = True) -> bool:
        if len(self.current_piece.rotations) <= 1:
            return False
        old_idx = self.current_piece.rotation_idx
        new_shape = Piece(self.current_piece.type)
        new_shape.rotation_idx = old_idx
        new_shape.matrix = self.current_piece.rotations[old_idx]
        new_shape.x = self.current_piece.x
        new_shape.y = self.current_piece.y
        step = 1 if clockwise else -1
        new_shape.rotation_idx = (old_idx + step) % len(new_shape.rotations)
        test = new_shape.rotations[new_shape.rotation_idx]
        for kick in ((0, 0), (-1, 0), (1, 0), (-2, 0), (2, 0)):
            if not self.check_collision(
                self.current_piece, kick[0], kick[1], test
            ):
                self.current_piece.rotation_idx = new_shape.rotation_idx
                self.current_piece.matrix = test
                self.current_piece.x += kick[0]
                self.current_piece.y += kick[1]
                return True
        return False

    def lock_piece(self) -> None:
        for r, row in enumerate(self.current_piece.matrix):
            for c, val in enumerate(row):
                if val:
                    py = self.current_piece.y + r
                    if py < 0:
                        self.game_over = True
                        self._update_high_score()
                        return
                    self.grid[py][self.current_piece.x + c] = self.current_piece.color_index
        lines_to_clear = [i for i, row in enumerate(self.grid) if all(row)]
        if lines_to_clear:
            self.pending_lines = lines_to_clear
            self.freeze_frames = FAMICOM_LINE_CLEAR_FRAMES
            self.freeze_reason = "line_clear"
            self.das_counter = 0
            self.das_dir = 0
            return
        self._spawn_next_after_lock()

    def _apply_line_clear(self) -> None:
        count = len(self.pending_lines)
        for index in sorted(self.pending_lines, reverse=True):
            del self.grid[index]
            self.grid.insert(0, [0] * GRID_WIDTH)
        self.pending_lines = []
        if count > 0:
            self.lines_cleared += count
            self.level = min(self.lines_cleared // 10, 29)
            scoring_map = {1: 40, 2: 100, 3: 300, 4: 1200}
            self.score += scoring_map.get(count, 0) * (self.level + 1)
        self._spawn_next_after_lock()

    def handle_input(self) -> str:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return "QUIT"
            if event.type != pygame.KEYDOWN:
                continue
            if self.game_over:
                if event.key == pygame.K_RETURN:
                    self.reset_game()
                elif event.key == pygame.K_ESCAPE:
                    return "MENU"
                continue
            if self._input_frozen():
                continue
            if event.key == pygame.K_LEFT and not self.check_collision(
                self.current_piece, offset_x=-1
            ):
                self.current_piece.x -= 1
            elif event.key == pygame.K_RIGHT and not self.check_collision(
                self.current_piece, offset_x=1
            ):
                self.current_piece.x += 1
            elif event.key == pygame.K_UP:
                self.try_rotate(clockwise=True)
            elif event.key == pygame.K_z:
                self.try_rotate(clockwise=False)
            elif event.key == pygame.K_SPACE:
                while not self.check_collision(self.current_piece, offset_y=1):
                    self.current_piece.y += 1
                    self.score += 2
                self.lock_piece()
            elif event.key == pygame.K_ESCAPE:
                return "MENU"
        return "PLAYING"

    def _try_shift(self, dx: int) -> None:
        if not self.check_collision(self.current_piece, offset_x=dx):
            self.current_piece.x += dx

    def _famicom_das(self) -> None:
        if self.freeze_frames > 0:
            return
        keys = pygame.key.get_pressed()
        direction = 0
        if keys[pygame.K_LEFT]:
            direction = -1
        elif keys[pygame.K_RIGHT]:
            direction = 1
        if direction == 0:
            self.das_dir = 0
            self.das_counter = 0
            return
        if direction != self.das_dir:
            self.das_dir = direction
            self.das_counter = 0
            self._try_shift(direction)
            return
        self.das_counter += 1
        if self.das_counter >= FAMICOM_DAS_DELAY:
            if (self.das_counter - FAMICOM_DAS_DELAY) % FAMICOM_DAS_REPEAT == 0:
                self._try_shift(direction)

    def update(self) -> None:
        """One Famicom frame @ 60 FPS — gravity/DAS/ARE in frames, not milliseconds."""
        if self.game_over:
            return
        if self.freeze_frames > 0:
            self.freeze_frames -= 1
            if self.freeze_frames == 0:
                if self.freeze_reason == "line_clear":
                    self._apply_line_clear()
                else:
                    self.freeze_reason = None
            return

        self._famicom_das()
        keys = pygame.key.get_pressed()
        if keys[pygame.K_DOWN] and not self.check_collision(self.current_piece, offset_y=1):
            self.current_piece.y += 1
            self.score += 1
            return

        self.drop_timer += 1
        if self.drop_timer >= famicom_gravity_frames(self.level):
            self.drop_timer = 0
            if not self.check_collision(self.current_piece, offset_y=1):
                self.current_piece.y += 1
            else:
                self.lock_piece()

    def _draw_playfield_blocks(self) -> None:
        _draw_well_grid(self.screen)
        for r in range(GRID_HEIGHT):
            for c in range(GRID_WIDTH):
                color = _color_for_grid(self.grid[r][c])
                if color:
                    _draw_beveled_block(
                        self.screen,
                        PLAY_X + c * BLOCK_SIZE,
                        PLAY_Y + r * BLOCK_SIZE,
                        color,
                    )
        if not self.game_over and self.freeze_reason != "line_clear":
            color = PIECE_COLORS[self.current_piece.type]
            for r, row in enumerate(self.current_piece.matrix):
                for c, val in enumerate(row):
                    if val:
                        _draw_beveled_block(
                            self.screen,
                            PLAY_X + (self.current_piece.x + c) * BLOCK_SIZE,
                            PLAY_Y + (self.current_piece.y + r) * BLOCK_SIZE,
                            color,
                        )
            _draw_drop_arrow(self.screen, self.current_piece)

    def _draw_sidebar(self) -> None:
        """Right column: SCORE, LINES, LEVEL, NEXT (yellow labels, white values)."""
        sx = SIDEBAR_X
        rows = (
            ("SCORE", f"{self.score:06d}", 100),
            ("LINES", f"{self.lines_cleared:03d}", 175),
            ("LEVEL", f"{self.level + 1:02d}", 250),
        )
        for label, value, y in rows:
            self.screen.blit(self.label_font.render(label, True, LABEL_YELLOW), (sx, y))
            self.screen.blit(self.value_font.render(value, True, WHITE), (sx, y + 22))

        self.screen.blit(self.label_font.render("NEXT", True, LABEL_YELLOW), (sx, 278))
        _draw_frame_box(self.screen, _RECT_NEXT_BOX)
        inner = _RECT_NEXT_BOX.inflate(-10, -10)
        pygame.draw.rect(self.screen, WELL_BG, inner)
        _draw_preview_piece(
            self.screen,
            self.next_piece.type,
            _RECT_NEXT_BOX.center,
        )

    def draw(self) -> None:
        self.screen.fill(BLACK)
        _draw_title(self.screen, self.title_font)
        _draw_frame_box(self.screen, _RECT_PLAY_FRAME)
        self._draw_playfield_blocks()
        self._draw_sidebar()

        if self.game_over:
            overlay = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 180))
            self.screen.blit(overlay, (0, 0))
            for text, y, font in (
                ("GAME OVER", SCREEN_HEIGHT // 2 - 40, self.large_font),
                ("Press ENTER to Retry", SCREEN_HEIGHT // 2 + 10, self.font),
                ("Press ESC for Menu", SCREEN_HEIGHT // 2 + 40, self.font),
            ):
                surf = font.render(text, True, WHITE if y < SCREEN_HEIGHT // 2 else LIGHT_GRAY)
                self.screen.blit(surf, (SCREEN_WIDTH // 2 - surf.get_width() // 2, y))

    def run(self) -> str:
        _GAME_AUDIO.stop()
        _GAME_AUDIO.play_game_music()
        state = "PLAYING"
        try:
            while state == "PLAYING":
                # One tick(FPS) = one NTSC Famicom frame; all game speeds use frame counts.
                self.clock.tick(FPS)
                state = self.handle_input()
                if state != "PLAYING":
                    break
                self.update()
                self.draw()
                pygame.display.flip()
        finally:
            _GAME_AUDIO.stop()
        return state if state in ("MENU", "QUIT") else "MENU"


# NES Tetris (1989) title-screen palette — procedural, FILES=OFF
_NES_BORDER_CELL = 10
_NES_BORDER_LIGHT = (186, 186, 186)
_NES_BORDER_MID = (118, 118, 118)
_NES_BORDER_DARK = (58, 58, 58)
_NES_LOGO_BANDS = ((0, 168, 0), (192, 0, 0), (0, 0, 200), (228, 204, 128))
_NES_CAT_GREEN = (0, 168, 0)
_NES_CAT_GOLD = (248, 216, 0)
_NES_CAT_RED = (200, 0, 0)
_NES_CAT_STONE = (180, 180, 180)

# Tetromino footprints for the grey frame (cell coords within each piece)
_NES_BORDER_PIECES: tuple[tuple[tuple[int, int], ...], ...] = (
    ((0, 0), (1, 0), (2, 0), (2, 1)),  # L
    ((0, 0), (0, 1), (1, 0), (2, 0)),  # J
    ((0, 0), (1, 0), (2, 0), (1, 1)),  # T
    ((0, 0), (1, 0), (0, 1), (1, 1)),  # O
    ((1, 0), (2, 0), (0, 1), (1, 1)),  # S
    ((0, 0), (1, 0), (1, 1), (2, 1)),  # Z
    ((1, 0), (0, 1), (1, 1), (2, 1)),  # skew
)


def _menu_font(size: int, *, bold: bool = False) -> pygame.font.Font:
    return pygame.font.SysFont("Courier New", size, bold=bold)


def _draw_nes_block(screen: pygame.Surface, x: int, y: int, size: int = _NES_BORDER_CELL) -> None:
    r = pygame.Rect(x, y, size, size)
    pygame.draw.rect(screen, _NES_BORDER_MID, r)
    pygame.draw.line(screen, _NES_BORDER_LIGHT, (x, y), (x + size - 1, y))
    pygame.draw.line(screen, _NES_BORDER_LIGHT, (x, y), (x, y + size - 1))
    pygame.draw.line(screen, _NES_BORDER_DARK, (x + size - 1, y), (x + size - 1, y + size - 1))
    pygame.draw.line(screen, _NES_BORDER_DARK, (x, y + size - 1), (x + size - 1, y + size - 1))


def _draw_nes_border(screen: pygame.Surface) -> None:
    """Grey tetromino-style frame like the 1989 NES title screen."""
    w, h = SCREEN_WIDTH, SCREEN_HEIGHT
    c = _NES_BORDER_CELL
    thick = c * 2
    pieces = _NES_BORDER_PIECES
    idx = 0

    def place_piece(ox: int, oy: int) -> None:
        nonlocal idx
        for cx, cy in pieces[idx % len(pieces)]:
            _draw_nes_block(screen, ox + cx * c, oy + cy * c, c)
        idx += 1

    x = 0
    while x < w:
        place_piece(x, 0)
        place_piece(x, h - thick)
        x += c * 3
    y = thick
    while y < h - thick:
        place_piece(0, y)
        place_piece(w - thick, y)
        y += c * 3


def _render_banded_glyph(font: pygame.font.Font, char: str) -> pygame.Surface:
    """One character with green / red / blue / gold horizontal bands."""
    mask = font.render(char, True, (255, 255, 255))
    gw, gh = mask.get_size()
    if gw == 0 or gh == 0:
        return mask
    out = pygame.Surface((gw, gh), pygame.SRCALPHA)
    n = len(_NES_LOGO_BANDS)
    for i, color in enumerate(_NES_LOGO_BANDS):
        y0 = i * gh // n
        y1 = gh if i == n - 1 else (i + 1) * gh // n
        if y1 <= y0:
            y1 = y0 + 1
        row = pygame.Surface((gw, gh), pygame.SRCALPHA)
        row.fill((*color, 255))
        row.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        band = row.subsurface((0, y0, gw, y1 - y0)).copy()
        out.blit(band, (0, y0))
    return out


def _nes_logo_font(lines: tuple[str, ...]) -> pygame.font.Font:
    pad = _NES_BORDER_CELL * 3
    max_w = SCREEN_WIDTH - pad * 2
    font = _menu_font(28, bold=True)
    for size in range(48, 22, -2):
        trial = _menu_font(size, bold=True)
        if all(sum(trial.size(ch)[0] for ch in line) <= max_w for line in lines):
            return trial
    return font


def _build_nes_logo_surface(lines: tuple[str, ...]) -> pygame.Surface:
    """Compose the full banded logo into one surface for reliable blitting."""
    font = _nes_logo_font(lines)
    row_surfaces: list[pygame.Surface] = []
    row_h_max = 0
    for line in lines:
        glyphs = [_render_banded_glyph(font, ch) for ch in line]
        row_h = max((g.get_height() for g in glyphs), default=0)
        row_h_max = max(row_h_max, row_h)
        total_w = sum(g.get_width() for g in glyphs)
        row_surf = pygame.Surface((total_w, row_h), pygame.SRCALPHA)
        x = 0
        for g in glyphs:
            row_surf.blit(g, (x, (row_h - g.get_height()) // 2))
            x += g.get_width()
        row_surfaces.append(row_surf)

    gap = 4
    total_h = sum(s.get_height() for s in row_surfaces) + gap * max(0, len(row_surfaces) - 1)
    total_w = max((s.get_width() for s in row_surfaces), default=0)
    logo = pygame.Surface((total_w, total_h), pygame.SRCALPHA)
    y = 0
    for row_surf in row_surfaces:
        logo.blit(row_surf, ((total_w - row_surf.get_width()) // 2, y))
        y += row_surf.get_height() + gap
    return logo


def _logo_for_display(screen: pygame.Surface, logo: pygame.Surface) -> pygame.Surface:
    """Bake SRCALPHA logo onto black so it blits reliably on RGB displays."""
    baked = pygame.Surface(logo.get_size())
    baked.fill(BLACK)
    baked.blit(logo, (0, 0))
    return baked.convert(screen)


def _draw_nes_logo(screen: pygame.Surface, logo: pygame.Surface, top_y: int) -> int:
    x = SCREEN_WIDTH // 2 - logo.get_width() // 2
    screen.blit(logo, (x, top_y))
    return top_y + logo.get_height()


def _nes_logo_center_y(logo: pygame.Surface) -> int:
    """Vertical position so the logo block is centered inside the border."""
    pad = _NES_BORDER_CELL * 3
    inner_h = SCREEN_HEIGHT - pad * 2
    return pad + (inner_h - logo.get_height()) // 2


def _draw_nes_cathedral(screen: pygame.Surface, ox: int, oy: int) -> None:
    """Saint Basil's Cathedral — simplified NES pixel art."""
    px = 4

    def dot(dx: int, dy: int, color: tuple[int, int, int]) -> None:
        pygame.draw.rect(screen, color, (ox + dx * px, oy + dy * px, px, px))

    # Base / walls
    for dx in range(0, 18):
        for dy in range(14, 20):
            if 2 <= dx <= 15:
                dot(dx, dy, _NES_CAT_STONE)
    for dx in range(4, 14):
        dot(dx, 13, _NES_CAT_STONE)

    # Central spire
    for dy in range(6, 14):
        dot(8, dy, _NES_CAT_GOLD)
        dot(9, dy, _NES_CAT_GOLD)
    dot(8, 5, _NES_CAT_RED)
    dot(9, 5, _NES_CAT_RED)
    dot(8, 4, _NES_CAT_GREEN)
    dot(9, 4, _NES_CAT_GREEN)

    # Side domes
    for base_x, dome_c in ((3, _NES_CAT_GREEN), (12, _NES_CAT_RED)):
        dot(base_x, 10, dome_c)
        dot(base_x + 1, 10, dome_c)
        dot(base_x, 9, _NES_CAT_GOLD)
        dot(base_x + 1, 9, _NES_CAT_GOLD)
        dot(base_x, 8, dome_c)

    dot(1, 11, _NES_CAT_GREEN)
    dot(2, 11, _NES_CAT_GREEN)
    dot(15, 11, _NES_CAT_RED)
    dot(16, 11, _NES_CAT_RED)
    dot(0, 12, _NES_CAT_GOLD)
    dot(17, 12, _NES_CAT_GOLD)


def _fit_menu_font(text: str, max_width: int, start: int = 22) -> pygame.font.Font:
    for size in range(start, 12, -2):
        font = _menu_font(size, bold=True)
        if font.render(text, True, WHITE).get_width() <= max_width:
            return font
    return _menu_font(12, bold=True)


def _blit_centered(screen: pygame.Surface, surf: pygame.Surface, y: int) -> None:
    x = SCREEN_WIDTH // 2 - surf.get_width() // 2
    screen.blit(surf, (x, y))


# Main menu entries (procedural tetromino cursor + synthesized SFX)
_MENU_ITEMS = ("PLAY GAME", "HELP", "SOUND", "SETTINGS", "EXIT GAME")
_MENU_CURSOR_PIECES = ("I", "T", "O", "S", "Z", "J", "L")


def _draw_menu_cursor(
    screen: pygame.Surface,
    shape_key: str,
    x: int,
    y: int,
    tick: int,
) -> None:
    """Animated tetromino selector — procedural asset, no images."""
    cell = 6
    cells = _PREVIEW_CELLS[shape_key]
    min_x = min(c for c, _ in cells)
    min_y = min(r for _, r in cells)
    wobble = (tick // 8) % 2
    color = PIECE_COLORS[shape_key]
    for cx, cy in cells:
        _draw_beveled_block(
            screen,
            x + (cx - min_x) * cell,
            y + (cy - min_y) * cell + wobble,
            color,
            cell,
        )


def _draw_menu_piece_deco(screen: pygame.Surface, tick: int) -> None:
    """Seven tetrominoes along the bottom border — FILES=OFF decoration."""
    pad = _NES_BORDER_CELL * 3
    y = SCREEN_HEIGHT - pad - 28
    gap = 72
    start_x = (SCREEN_WIDTH - gap * 6) // 2
    for i, key in enumerate(_MENU_CURSOR_PIECES):
        _draw_menu_cursor(screen, key, start_x + i * gap, y, tick + i * 11)


def _draw_menu_list(
    screen: pygame.Surface,
    selected: int,
    tick: int,
    item_font: pygame.font.Font,
) -> int:
    """Returns bottom Y of the menu block."""
    row_h = 34
    start_y = 268
    label_w = max(item_font.size(label)[0] for label in _MENU_ITEMS)
    block_w = label_w + 56
    left = SCREEN_WIDTH // 2 - block_w // 2

    for i, label in enumerate(_MENU_ITEMS):
        y = start_y + i * row_h
        active = i == selected
        if active:
            _draw_menu_cursor(
                screen,
                _MENU_CURSOR_PIECES[i % len(_MENU_CURSOR_PIECES)],
                left - 34,
                y + 4,
                tick,
            )
        color = _NES_CAT_GOLD if active else LIGHT_GRAY
        text = item_font.render(label, True, color)
        screen.blit(text, (SCREEN_WIDTH // 2 - text.get_width() // 2, y))
    return start_y + len(_MENU_ITEMS) * row_h


def _draw_sub_panel(
    screen: pygame.Surface,
    title: str,
    lines: tuple[str, ...],
) -> None:
    pad = _NES_BORDER_CELL * 3
    panel = pygame.Rect(pad + 20, 250, SCREEN_WIDTH - (pad + 20) * 2, 200)
    pygame.draw.rect(screen, BLACK, panel)
    pygame.draw.rect(screen, _NES_BORDER_LIGHT, panel, 2)
    pygame.draw.rect(screen, _NES_BORDER_MID, panel.inflate(-4, -4), 1)
    title_font = _menu_font(18, bold=True)
    body_font = _menu_font(14, bold=True)
    screen.blit(title_font.render(title, True, _NES_CAT_GOLD), (panel.x + 12, panel.y + 10))
    y = panel.y + 38
    for line in lines:
        screen.blit(body_font.render(line, True, WHITE), (panel.x + 16, y))
        y += 22
    hint = body_font.render("ESC · BACK", True, LIGHT_GRAY)
    screen.blit(hint, (panel.centerx - hint.get_width() // 2, panel.bottom - 28))


def _run_help_screen(screen: pygame.Surface, clock: pygame.time.Clock) -> None:
    lines = (
        "← → move   ↑ rotate   Z spin CCW",
        "↓ soft drop   SPACE hard drop",
        "ENTER · start game from menu",
        "FAMICOM 60 FPS · gravity + DAS + ARE",
        "FILES=OFF · no PNG/WAV on disk",
    )
    while True:
        screen.fill(BLACK)
        _draw_nes_border(screen)
        _draw_sub_panel(screen, "HELP", lines)
        pygame.display.flip()
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                _GAME_AUDIO.menu_cancel()
                return
        clock.tick(FPS)


def _run_sound_screen(screen: pygame.Surface, clock: pygame.time.Clock) -> None:
    row = 0
    rows = ("MUSIC", "SFX", "MUSIC VOLUME", "SFX VOLUME")
    while True:
        screen.fill(BLACK)
        _draw_nes_border(screen)
        status = (
            f"MUSIC: {'ON' if _SETTINGS.music_enabled else 'OFF'}",
            f"SFX: {'ON' if _SETTINGS.sfx_enabled else 'OFF'}",
            f"MUSIC VOL: {int(_SETTINGS.music_volume * 100)}%",
            f"SFX VOL: {int(_SETTINGS.sfx_volume * 100)}%",
            "",
            "↑↓ select   ←→ change   ENTER toggle",
            "ESC · back",
        )
        _draw_sub_panel(screen, "SOUND", status)
        y0 = 288
        for i, name in enumerate(rows):
            y = y0 + i * 22
            color = _NES_CAT_GOLD if i == row else LIGHT_GRAY
            screen.blit(_menu_font(14, bold=True).render(name, True, color), (120, y))
        pygame.display.flip()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return
            if event.type != pygame.KEYDOWN:
                continue
            if event.key == pygame.K_ESCAPE:
                _GAME_AUDIO.menu_cancel()
                return
            if event.key == pygame.K_UP:
                row = (row - 1) % len(rows)
                _GAME_AUDIO.menu_move()
            elif event.key == pygame.K_DOWN:
                row = (row + 1) % len(rows)
                _GAME_AUDIO.menu_move()
            elif event.key in (pygame.K_LEFT, pygame.K_RIGHT):
                delta = -0.05 if event.key == pygame.K_LEFT else 0.05
                if row == 0:
                    _SETTINGS.music_enabled = not _SETTINGS.music_enabled
                    _GAME_AUDIO.menu_select()
                elif row == 1:
                    _SETTINGS.sfx_enabled = not _SETTINGS.sfx_enabled
                    _GAME_AUDIO.menu_select()
                elif row == 2:
                    _SETTINGS.music_volume = max(0.0, min(1.0, _SETTINGS.music_volume + delta))
                    _GAME_AUDIO.menu_move()
                elif row == 3:
                    _SETTINGS.sfx_volume = max(0.0, min(1.0, _SETTINGS.sfx_volume + delta))
                    _GAME_AUDIO.menu_move()
            elif event.key == pygame.K_RETURN:
                if row == 0:
                    _SETTINGS.music_enabled = not _SETTINGS.music_enabled
                elif row == 1:
                    _SETTINGS.sfx_enabled = not _SETTINGS.sfx_enabled
                _GAME_AUDIO.menu_select()
        clock.tick(FPS)


def _run_settings_screen(screen: pygame.Surface, clock: pygame.time.Clock) -> None:
    lines = (
        f"TITLE: {APP_TITLE}",
        "MODE: A-TYPE (Famicom rules)",
        f"DISPLAY: {SCREEN_WIDTH}×{SCREEN_HEIGHT} @ {FAMICOM_FPS} FPS",
        "ASSETS: procedural tetromino UI only",
        "FILES=OFF (no PNG / OGG / WAV)",
        "",
        "ESC · back",
    )
    while True:
        screen.fill(BLACK)
        _draw_nes_border(screen)
        _draw_sub_panel(screen, "SETTINGS", lines)
        pygame.display.flip()
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                _GAME_AUDIO.menu_cancel()
                return
        clock.tick(FPS)


def main_menu(screen: pygame.Surface) -> str:
    """NES title + tetromino menu (PLAY / HELP / SOUND / SETTINGS / EXIT)."""
    clock = pygame.time.Clock()
    pygame.display.set_caption(APP_TITLE)
    _GAME_AUDIO.stop()

    pad = _NES_BORDER_CELL * 3
    item_font = _fit_menu_font("PLAY GAME", SCREEN_WIDTH - pad * 2, 20)
    small_font = _menu_font(14, bold=True)
    copy_surf = small_font.render(f"© {APP_TITLE}", True, WHITE).convert_alpha()
    hint_surf = small_font.render("↑↓ · MOVE   ENTER · SELECT   ESC · BACK", True, LIGHT_GRAY)
    cat_ox = SCREEN_WIDTH - 18 * 4 - pad
    cat_oy = SCREEN_HEIGHT - 20 * 4 - pad
    logo_surf = _logo_for_display(screen, _build_nes_logo_surface(MENU_TITLE_LINES))
    logo_y = 88
    tm = _menu_font(10, bold=True).render("TM", True, _NES_LOGO_BANDS[0])
    tm_x = SCREEN_WIDTH // 2 + logo_surf.get_width() // 2 + 4
    selected = 0
    tick = 0

    while True:
        screen.fill(BLACK)
        _draw_nes_border(screen)
        _draw_nes_logo(screen, logo_surf, logo_y)
        screen.blit(tm, (min(tm_x, SCREEN_WIDTH - pad - tm.get_width()), logo_y + 4))
        menu_bottom = _draw_menu_list(screen, selected, tick, item_font)
        _draw_menu_piece_deco(screen, tick)
        _draw_nes_cathedral(screen, cat_ox, cat_oy)
        _blit_centered(screen, hint_surf, menu_bottom + 8)
        _blit_centered(screen, copy_surf, SCREEN_HEIGHT - pad - copy_surf.get_height())
        pygame.display.flip()
        tick += 1

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return "QUIT"
            if event.type != pygame.KEYDOWN:
                continue
            if event.key == pygame.K_UP:
                selected = (selected - 1) % len(_MENU_ITEMS)
                _GAME_AUDIO.menu_move()
            elif event.key == pygame.K_DOWN:
                selected = (selected + 1) % len(_MENU_ITEMS)
                _GAME_AUDIO.menu_move()
            elif event.key == pygame.K_RETURN:
                _GAME_AUDIO.menu_select()
                choice = _MENU_ITEMS[selected]
                if choice == "PLAY GAME":
                    return "GAME"
                if choice == "EXIT GAME":
                    return "QUIT"
                if choice == "HELP":
                    _run_help_screen(screen, clock)
                elif choice == "SOUND":
                    _run_sound_screen(screen, clock)
                elif choice == "SETTINGS":
                    _run_settings_screen(screen, clock)
            elif event.key == pygame.K_ESCAPE:
                return "QUIT"
        clock.tick(FPS)


def _shutdown() -> None:
    _GAME_AUDIO.stop()
    if pygame.get_init():
        pygame.quit()


def main() -> None:
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption(APP_TITLE)
    state = "MENU"
    try:
        while state not in ("QUIT", None):
            if state == "MENU":
                state = main_menu(screen)
            elif state == "GAME":
                state = TetrisGame(screen).run()
    finally:
        _shutdown()


if __name__ == "__main__":
    main()
