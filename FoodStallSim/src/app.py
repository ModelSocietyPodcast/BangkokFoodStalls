"""
Streamlit stage preview (NO simulation run):
- Builds a Mesa model with a 20x22 grid
- Composites sky + stage into a single image layer

Run:
  pip install mesa streamlit pillow
  streamlit run st_stage_preview.py
"""

from __future__ import annotations

import io
import json
import random
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import streamlit as st
from streamlit.components.v1 import html
from PIL import Image, ImageDraw

import mesa
from mesa.space import MultiGrid

SIM_MINUTES_PER_STEP = 30
REAL_SECONDS_PER_STEP = 5
SIM_START_MINUTES = 6 * 60
SIM_END_MINUTES = 24 * 60


def format_sim_time(total_minutes: int) -> str:
    if total_minutes == 24 * 60:
        return "24:00"
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours:02d}:{minutes:02d}"


def get_sky_filename(total_minutes: int) -> str:
    if 6 * 60 <= total_minutes < 8 * 60:
        return "Sky_Dawn.png"
    if 8 * 60 <= total_minutes < 12 * 60:
        return "Sky_Morning.png"
    if 12 * 60 <= total_minutes < 17 * 60:
        return "Sky_Afternoon.png"
    if 17 * 60 <= total_minutes < 19 * 60:
        return "Sky_LateAfternoon.png"
    if 19 * 60 <= total_minutes < 21 * 60:
        return "Sky_Dusk.png"
    return "Sky_Night.png"


def load_stall_names(path: Path) -> list[str]:
    names = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return [name for name in names if name]


def load_dish_catalog(path: Path) -> list[tuple[str, str]]:
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip()
        if not cleaned or ";" not in cleaned:
            continue
        dish, category = (part.strip() for part in cleaned.split(";", 1))
        if dish and category:
            entries.append((dish, category))
    return entries


class FrameRunner:
    def __init__(
        self,
        stage_path: Path,
        stalls_dir: Path,
        people_dir: Path,
        cols: int,
        rows: int,
        stall_names: list[str],
        dishes: list[tuple[str, str]],
    ):
        self._lock = threading.RLock()
        self._stage_path = stage_path
        self._stalls_dir = stalls_dir
        self._cols = cols
        self._rows = rows
        self._sim_minutes = SIM_START_MINUTES
        self._clock_running = False
        self._thread = None
        self._last_frame = b""
        self._stage_img = Image.open(stage_path).convert("RGBA")
        self._display_width = self._stage_img.width // 2
        self._display_height = self._stage_img.height // 2
        self._sky_cache: dict[Path, Image.Image] = {}
        self._grid_overlay: Image.Image | None = None
        self._sprite_cache: dict[tuple[Path, tuple[int, int]], Image.Image] = {}
        self._people_cache: dict[tuple[Path, tuple[int, int]], Image.Image] = {}
        stall_coords = [
            (1, 10),
            (4, 10),
            (7, 10),
            (10, 10),
            (13, 10),
            (16, 10),
        ]
        self._stall_names = [
            random.choice(stall_names) for _ in range(len(stall_coords))
        ]
        self._stall_dishes = [
            random.choice(dishes) for _ in range(len(stall_coords))
        ]
        stall_sprite_paths = sorted(stalls_dir.glob("*.png"))
        people_sprite_paths = sorted(people_dir.glob("*.png"))
        self._stall_assignments = [
            (coord, random.choice(stall_sprite_paths)) for coord in stall_coords
        ]
        self._people_assignments = self._assign_people(stall_coords, people_sprite_paths)
        self._port = self._start_server()
        self._render_frame()

    @property
    def port(self) -> int:
        return self._port

    @property
    def display_width(self) -> int:
        return self._display_width

    @property
    def display_height(self) -> int:
        return self._display_height

    def start(self) -> None:
        with self._lock:
            self._clock_running = True
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._clock_running = False

    def advance_once(self) -> None:
        with self._lock:
            if self._sim_minutes >= SIM_END_MINUTES:
                return
            self._sim_minutes = min(
                self._sim_minutes + SIM_MINUTES_PER_STEP,
                SIM_END_MINUTES,
            )
        self._render_frame()

    def get_state(self) -> dict[str, object]:
        with self._lock:
            return {
                "sim_minutes": self._sim_minutes,
                "sim_time": format_sim_time(self._sim_minutes),
                "clock_running": self._clock_running,
            }

    def get_frame_bytes(self) -> bytes:
        with self._lock:
            return self._last_frame

    def get_stall_names(self) -> list[str]:
        with self._lock:
            return list(self._stall_names)

    def get_stall_dishes(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._stall_dishes)

    def _run(self) -> None:
        while True:
            with self._lock:
                running = self._clock_running
                at_end = self._sim_minutes >= SIM_END_MINUTES
            if not running or at_end:
                break
            self.advance_once()
            time.sleep(REAL_SECONDS_PER_STEP)

    def _render_frame(self) -> None:
        with self._lock:
            sky_path = (
                self._stage_path.parent / get_sky_filename(self._sim_minutes)
            )
            background = self._get_background(sky_path)
            frame = background.copy()
            cell_w = frame.width / self._cols
            cell_h = frame.height / self._rows
            for (x, y), sprite_path in self._stall_assignments:
                sprite = self._get_sprite(sprite_path, cell_w, cell_h)
                left = round(x * cell_w)
                right = left + sprite.width
                top = round(frame.height - (y + 3) * cell_h)
                frame.paste(sprite, (left, top), sprite)
            for x, y, sprite_path in self._people_assignments:
                person = self._get_person(sprite_path, cell_w, cell_h)
                left = round(x * cell_w)
                top = round(frame.height - (y + 1) * cell_h)
                frame.paste(person, (left, top), person)
            grid_overlay = self._get_grid_overlay(frame.size)
            frame = Image.alpha_composite(frame, grid_overlay)
            frame_bytes = io.BytesIO()
            frame.save(frame_bytes, format="PNG")
            self._last_frame = frame_bytes.getvalue()

    def _get_background(self, sky_path: Path) -> Image.Image:
        sky = self._sky_cache.get(sky_path)
        if sky is None:
            sky = Image.open(sky_path).convert("RGBA")
            self._sky_cache[sky_path] = sky
        if sky.size != self._stage_img.size:
            sky = sky.resize(self._stage_img.size)
        background = sky.copy()
        background.paste(self._stage_img, (0, 0), self._stage_img)
        return background

    def _get_grid_overlay(self, size: tuple[int, int]) -> Image.Image:
        if self._grid_overlay is not None:
            return self._grid_overlay
        overlay = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        cell_w = size[0] / self._cols
        cell_h = size[1] / self._rows
        for x in range(self._cols + 1):
            x_pos = round(x * cell_w)
            draw.line([(x_pos, 0), (x_pos, size[1])], fill=(255, 255, 255, 160), width=1)
        for y in range(self._rows + 1):
            y_pos = round(y * cell_h)
            draw.line([(0, y_pos), (size[0], y_pos)], fill=(255, 255, 255, 160), width=1)
        self._grid_overlay = overlay
        return overlay

    def _get_sprite(self, sprite_path: Path, cell_w: float, cell_h: float) -> Image.Image:
        size = (round(3 * cell_w), round(3 * cell_h))
        key = (sprite_path, size)
        sprite = self._sprite_cache.get(key)
        if sprite is None:
            sprite = Image.open(sprite_path).convert("RGBA").resize(size)
            self._sprite_cache[key] = sprite
        return sprite

    def _get_person(self, sprite_path: Path, cell_w: float, cell_h: float) -> Image.Image:
        size = (round(cell_w), round(cell_h))
        key = (sprite_path, size)
        sprite = self._people_cache.get(key)
        if sprite is None:
            sprite = Image.open(sprite_path).convert("RGBA").resize(size)
            self._people_cache[key] = sprite
        return sprite

    def _assign_people(
        self,
        stall_coords: list[tuple[int, int]],
        people_paths: list[Path],
    ) -> list[tuple[int, int, Path]]:
        assignments: list[tuple[int, int, Path]] = []
        if not people_paths:
            return assignments
        for x, y in stall_coords:
            start_x = x + 1
            start_y = y - 1
            for offset in range(5):
                person_x = start_x
                person_y = start_y - offset
                if 0 <= person_x < self._cols and 0 <= person_y < self._rows:
                    assignments.append(
                        (person_x, person_y, random.choice(people_paths))
                    )
        return assignments

    def _start_server(self) -> int:
        runner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/frame.png":
                    data = runner.get_frame_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if parsed.path == "/state.json":
                    state = runner.get_state()
                    payload = json.dumps(state).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_response(404)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                return

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return port


@st.cache_resource(show_spinner=False)
def get_runner(
    stage_path: Path,
    stalls_dir: Path,
    people_dir: Path,
    cols: int,
    rows: int,
    stall_names: tuple[str, ...],
    dishes: tuple[tuple[str, str], ...],
) -> FrameRunner:
    return FrameRunner(
        stage_path,
        stalls_dir,
        people_dir,
        cols,
        rows,
        list(stall_names),
        list(dishes),
    )

# -----------------------------
# 1) Minimal Mesa model
# -----------------------------
class StageOnlyModel(mesa.Model):
    def __init__(self, width: int = 20, height: int = 22, seed=None):
        super().__init__(seed=seed)
        self.width = width
        self.height = height
        self.grid = MultiGrid(width, height, torus=False)

    def step(self):
        # Intentionally empty
        pass


# -----------------------------
# 2) Streamlit UI
# -----------------------------
st.set_page_config(page_title="Food Stall Sim - Stage Preview", layout="wide")
html(
    """
    <script>
    const key = "scroll-pos";
    const saved = localStorage.getItem(key);
    if (saved !== null) {
        const y = parseInt(saved, 10);
        if (!Number.isNaN(y)) {
            requestAnimationFrame(() => window.scrollTo(0, y));
        }
    }
    const persistScroll = () => {
        localStorage.setItem(key, window.scrollY.toString());
    };
    window.addEventListener("scroll", persistScroll, { passive: true });
    document.addEventListener("visibilitychange", persistScroll);
    setInterval(persistScroll, 500);
    </script>
    """,
    height=0,
    width=0,
)

col_left, col_right = st.columns([1, 2], gap="small")
# Paths to layered images
ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets" / "sprites" / "skyline"
STAGE_PATH = ASSETS_DIR / "Stage.png"
STALLS_DIR = Path(__file__).resolve().parents[1] / "assets" / "sprites" / "stalls"
PEOPLE_DIR = Path(__file__).resolve().parents[1] / "assets" / "sprites" / "people"
STALL_NAMES_PATH = Path(__file__).resolve().parents[1] / "assets" / "stall_names.txt"
DISH_CATALOG_PATH = Path(__file__).resolve().parents[1] / "assets" / "dish_catalog.txt"

if not STAGE_PATH.exists():
    st.error(f"Couldn't find stage image at: {STAGE_PATH}")
    st.info("Create ./assets/sprites/skyline/Stage.png (or change STAGE_PATH in the script).")
    st.stop()

stall_sprite_paths = sorted(STALLS_DIR.glob("*.png"))
if not stall_sprite_paths:
    st.error(f"Couldn't find stall sprites in: {STALLS_DIR}")
    st.info("Add .png files to ./assets/sprites/stalls (or change STALLS_DIR in the script).")
    st.stop()

people_sprite_paths = sorted(PEOPLE_DIR.glob("*.png"))
if not people_sprite_paths:
    st.error(f"Couldn't find people sprites in: {PEOPLE_DIR}")
    st.info("Add .png files to ./assets/sprites/people (or change PEOPLE_DIR in the script).")
    st.stop()

if not STALL_NAMES_PATH.exists():
    st.error(f"Couldn't find stall names file at: {STALL_NAMES_PATH}")
    st.info("Create ./assets/stall_names.txt with one stall name per line.")
    st.stop()

stall_names = load_stall_names(STALL_NAMES_PATH)
if not stall_names:
    st.error(f"No stall names found in: {STALL_NAMES_PATH}")
    st.info("Add at least one stall name to ./assets/stall_names.txt.")
    st.stop()

dishes = load_dish_catalog(DISH_CATALOG_PATH)
if not dishes:
    st.error(f"No dishes found in: {DISH_CATALOG_PATH}")
    st.info("Add dish entries as 'Dish; Category' to ./assets/dish_catalog.txt.")
    st.stop()

# Create model (truth source)
model = StageOnlyModel(width=20, height=22)
runner = get_runner(
    STAGE_PATH,
    STALLS_DIR,
    PEOPLE_DIR,
    model.width,
    model.height,
    tuple(stall_names),
    tuple(dishes),
)
display_width = runner.display_width
display_height = runner.display_height
poll_ms = max(500, int(REAL_SECONDS_PER_STEP * 1000 / 2))

with col_right:
    st.markdown('<div class="grid-anchor"></div>', unsafe_allow_html=True)
    st.markdown('<div class="grid-wrap">', unsafe_allow_html=True)
    html(
        f"""
        <style>
        body {{
            margin: 0;
        }}
        </style>
        <img
            id="grid-frame"
            src="http://127.0.0.1:{runner.port}/frame.png"
            style="width: {display_width}px; height: auto; display: block;"
        />
        <script>
        const img = document.getElementById("grid-frame");
        const base = "http://127.0.0.1:{runner.port}/frame.png";
        const pollMs = {poll_ms};
        function refresh() {{
            img.src = base + "?t=" + Date.now();
        }}
        refresh();
        setInterval(refresh, pollMs);
        </script>
        """,
        height=display_height + 16,
        width=display_width + 16,
    )
    st.markdown("</div>", unsafe_allow_html=True)

# -----------------------------
# 3) Layout + controls
# -----------------------------
with col_left:
    st.title("Yaowarat Food Stall Simulation")
    st.caption(
        "Happy Birthday, Rameet!! This is a simulation of the food stall mecca in "
        "Yaowarat. Each day, proprietors set up at dawn, bringing their unique dishes "
        "to hungry customers. The simulation follows what happens over a single day "
        "along a representative stretch along Bang Rak & Charoen Krung Road from 6 am "
        "to midnight. You can use the buttons provided to advance and stop time, "
        "watching customers come and go."
    )

    st.markdown("### Simulation Time")
    html(
        f"""
        <style>
        body {{
            margin: 0;
            font-family: "Source Sans Pro", sans-serif;
        }}
        </style>
        <div id="sim-time">Sim time: --</div>
        <script>
        const label = document.getElementById("sim-time");
        const url = "http://127.0.0.1:{runner.port}/state.json";
        async function updateTime() {{
            try {{
                const resp = await fetch(url, {{ cache: "no-store" }});
                const data = await resp.json();
                label.textContent = "Sim time: " + data.sim_time;
            }} catch (err) {{
                // Ignore transient fetch errors.
            }}
        }}
        updateTime();
        setInterval(updateTime, {poll_ms});
        </script>
        """,
        height=24,
    )

    col_start, col_stop, col_advance = st.columns(3)
    with col_start:
        if st.button("Start Clock"):
            runner.start()
    with col_stop:
        if st.button("Stop Clock"):
            runner.stop()
    with col_advance:
        if st.button("Advance 30 min"):
            runner.advance_once()

    st.markdown("### About")
    st.markdown(
        f"""
        <style>
        .block-container {{
            padding-top: 0;
            padding-left: 0.5rem;
            padding-right: 0;
            max-width: 100%;
        }}
        div[data-testid="stHorizontalBlock"]:has(.grid-anchor) {{
            display: flex;
            justify-content: center;
            column-gap: 0.25rem;
            align-items: flex-start;
        }}
        div[data-testid="stHorizontalBlock"]:has(.grid-anchor) > div:nth-child(1) {{
            flex: 1 1 0;
            max-width: 720px;
            min-width: 0;
        }}
        div[data-testid="stHorizontalBlock"]:has(.grid-anchor) > div:nth-child(2) {{
            flex: 0 0 {display_width + 16}px;
        }}
        .grid-wrap {{
            display: flex;
            justify-content: flex-end;
        }}
        div[data-testid="stTextArea"] textarea {{
            color: #000000;
            background-color: #f4c2c2;
            opacity: 1;
            -webkit-text-fill-color: #000000;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.text_area(
        "Simulation message",
        value="Stage preview with layered sky backgrounds. No simulation is executed.",
        height=200,
        disabled=True,
        label_visibility="collapsed",
    )

    st.markdown("### Food Stall Stats")
    stall_rows = []
    stall_names = runner.get_stall_names()
    stall_dishes = runner.get_stall_dishes()
    for index, stall_name in enumerate(stall_names):
        dish, category = stall_dishes[index]
        specialty = f"{dish} ({category})"
        stall_rows.append(
            {
                "Stall Name": stall_name,
                "Specialty Dish": specialty,
                "Revenue so Far": "฿0",
                "Average Service Time": "0 min",
            }
        )
    st.table(stall_rows)
