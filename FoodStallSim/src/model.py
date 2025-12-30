"""Simulation core helpers and rendering model."""

from __future__ import annotations

import io
import json
import random
import socket
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw

SIM_MINUTES_PER_STEP = 6
REAL_SECONDS_PER_STEP = 1
SIM_START_MINUTES = 6 * 60
SIM_END_MINUTES = 24 * 60
SERVICE_TIME_OPTIONS = [6, 12, 18, 24, 30]
BIKE_SPAWN_CHANCE = 0.01
BIKE_MOVE_CELLS = 2
BIKE_SIZE_CELLS = 2
PRICE_GAMMA_SHAPE = 5.0
CATEGORY_MEAN_PRICES = {
    "Savory": 80,
    "Dessert": 50,
    "Beverage": 30,
    "Grill": 90,
}


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


@dataclass
class QueuePerson:
    sprite_path: Path
    remaining_minutes: int
    service_minutes: int


@dataclass
class Bike:
    sprite_path: Path
    x: int
    y: int
    dx: int
    from_left: bool


def draw_dirichlet(size: int, alpha: float = 1.0) -> list[float]:
    samples = [random.gammavariate(alpha, 1.0) for _ in range(size)]
    total = sum(samples) or 1.0
    return [sample / total for sample in samples]


def sample_service_time(options: list[int], weights: list[float]) -> int:
    roll = random.random()
    cumulative = 0.0
    for option, weight in zip(options, weights):
        cumulative += weight
        if roll <= cumulative:
            return option
    return options[-1]


def draw_price_for_category(category: str) -> int:
    mean_price = CATEGORY_MEAN_PRICES.get(category, 70)
    scale = mean_price / PRICE_GAMMA_SHAPE
    price = random.gammavariate(PRICE_GAMMA_SHAPE, scale)
    return max(10, int(round(price)))


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
        self._stall_coords = [
            (1, 10),
            (4, 10),
            (7, 10),
            (10, 10),
            (13, 10),
            (16, 10),
        ]
        self._stall_names = [
            random.choice(stall_names) for _ in range(len(self._stall_coords))
        ]
        self._stall_dishes = [
            random.choice(dishes) for _ in range(len(self._stall_coords))
        ]
        self._stall_prices = [
            draw_price_for_category(category) for _, category in self._stall_dishes
        ]
        self._stall_served_counts = [0 for _ in range(len(self._stall_coords))]
        self._stall_total_service_minutes = [0 for _ in range(len(self._stall_coords))]
        self._stall_revenue = [0 for _ in range(len(self._stall_coords))]
        stall_sprite_paths = sorted(stalls_dir.glob("*.png"))
        self._people_paths = sorted(people_dir.glob("*.png"))
        self._stall_assignments = [
            (coord, random.choice(stall_sprite_paths)) for coord in self._stall_coords
        ]
        other_dir = stalls_dir.parent / "other"
        self._bike_left_paths = [
            other_dir / "MBike_1b.png",
            other_dir / "MBike_2b.png",
        ]
        self._bike_right_paths = [
            other_dir / "MBike_1a.png",
            other_dir / "MBike_2a.png",
        ]
        self._bike_left_paths = [path for path in self._bike_left_paths if path.exists()]
        self._bike_right_paths = [
            path for path in self._bike_right_paths if path.exists()
        ]
        self._has_bikes = bool(self._bike_left_paths or self._bike_right_paths)
        self._bike_spawn_chance = BIKE_SPAWN_CHANCE
        self._money_path = stalls_dir.parent / "other" / "Money.png"
        self._has_money = self._money_path.exists()
        self._service_time_options = list(SERVICE_TIME_OPTIONS)
        self._stall_service_distributions = [
            draw_dirichlet(len(self._service_time_options))
            for _ in range(len(self._stall_coords))
        ]
        self._queues = self._build_queues()
        self._bikes: list[Bike] = []
        self._sale_flash = [0 for _ in range(len(self._stall_coords))]
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
            self._tick_queues()
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

    def get_stall_revenue(self) -> list[int]:
        with self._lock:
            return list(self._stall_revenue)

    def get_stall_prices(self) -> list[int]:
        with self._lock:
            return list(self._stall_prices)

    def get_stall_average_service_times(self) -> list[int]:
        with self._lock:
            averages: list[int] = []
            for total_minutes, served_count in zip(
                self._stall_total_service_minutes,
                self._stall_served_counts,
            ):
                if served_count == 0:
                    averages.append(0)
                else:
                    averages.append(int(round(total_minutes / served_count)))
            return averages

    def get_stall_stats(self) -> list[dict[str, object]]:
        with self._lock:
            stats = []
            averages = self.get_stall_average_service_times()
            for index, stall_name in enumerate(self._stall_names):
                dish, category = self._stall_dishes[index]
                stats.append(
                    {
                        "stall_name": stall_name,
                        "specialty": f"{dish} ({category})",
                        "price": self._stall_prices[index],
                        "revenue": self._stall_revenue[index],
                        "avg_service_time": averages[index],
                    }
                )
            return stats

    def set_bike_spawn_chance(self, chance: float) -> None:
        with self._lock:
            self._bike_spawn_chance = max(0.0, min(1.0, float(chance)))

    def _run(self) -> None:
        while True:
            with self._lock:
                running = self._clock_running
                at_end = self._sim_minutes >= SIM_END_MINUTES
            if not running or at_end:
                break
            self.advance_once()
            time.sleep(REAL_SECONDS_PER_STEP)

    def _build_queues(self) -> list[list[QueuePerson]]:
        queues: list[list[QueuePerson]] = []
        if not self._people_paths:
            return queues
        for index in range(len(self._stall_coords)):
            distribution = self._stall_service_distributions[index]
            queue: list[QueuePerson] = []
            for _ in range(5):
                sprite = random.choice(self._people_paths)
                prep_time = sample_service_time(
                    self._service_time_options,
                    distribution,
                )
                queue.append(QueuePerson(sprite, prep_time, prep_time))
            queues.append(queue)
        return queues

    def _tick_queues(self) -> None:
        if not self._queues:
            return
        for index, remaining in enumerate(self._sale_flash):
            if remaining > 0:
                self._sale_flash[index] = remaining - 1
        for index, queue in enumerate(self._queues):
            if not queue:
                continue
            queue[0].remaining_minutes -= SIM_MINUTES_PER_STEP
            if queue[0].remaining_minutes <= 0:
                served = queue.pop(0)
                self._stall_served_counts[index] += 1
                self._stall_total_service_minutes[index] += served.service_minutes
                self._stall_revenue[index] += self._stall_prices[index]
                if self._has_money:
                    self._sale_flash[index] = 1
                sprite = random.choice(self._people_paths)
                prep_time = sample_service_time(
                    self._service_time_options,
                    self._stall_service_distributions[index],
                )
                queue.append(QueuePerson(sprite, prep_time, prep_time))
        self._tick_bikes()

    def _tick_bikes(self) -> None:
        if not self._has_bikes:
            return
        moved: list[Bike] = []
        for bike in self._bikes:
            next_x = bike.x + bike.dx
            if 0 <= next_x < self._cols:
                bike.x = next_x
                moved.append(bike)
        self._bikes = moved
        stall_rows = [y for _, y in self._stall_coords]
        if not stall_rows:
            return
        start_row = min(stall_rows) - 1
        for row in range(start_row, -1, -1):
            if random.random() >= self._bike_spawn_chance:
                continue
            enter_from_left = random.random() < 0.5
            if enter_from_left and self._bike_left_paths:
                sprite_path = random.choice(self._bike_left_paths)
                self._bikes.append(
                    Bike(sprite_path, 0, row, BIKE_MOVE_CELLS, True)
                )
            elif self._bike_right_paths:
                sprite_path = random.choice(self._bike_right_paths)
                self._bikes.append(
                    Bike(sprite_path, self._cols - 1, row, -BIKE_MOVE_CELLS, False)
                )

    def _render_frame(self) -> None:
        with self._lock:
            sky_path = self._stage_path.parent / get_sky_filename(self._sim_minutes)
            background = self._get_background(sky_path)
            frame = background.copy()
            cell_w = frame.width / self._cols
            cell_h = frame.height / self._rows
            for (x, y), sprite_path in self._stall_assignments:
                sprite = self._get_sprite(sprite_path, cell_w, cell_h)
                left = round(x * cell_w)
                top = round(frame.height - (y + 3) * cell_h)
                frame.paste(sprite, (left, top), sprite)
            if self._has_money:
                for index, (coord, _) in enumerate(self._stall_assignments):
                    if self._sale_flash[index] <= 0:
                        continue
                    x, y = coord
                    money_x = x + 2
                    money_y = y + 3
                    if 0 <= money_x < self._cols and 0 <= money_y < self._rows:
                        money = self._get_person(self._money_path, cell_w, cell_h)
                        left = round(money_x * cell_w)
                        top = round(frame.height - (money_y + 1) * cell_h)
                        frame.paste(money, (left, top), money)
            for index, (coord, _) in enumerate(self._stall_assignments):
                x, y = coord
                start_x = x + 1
                start_y = y - 1
                queue = self._queues[index] if index < len(self._queues) else []
                for offset, person_data in enumerate(queue):
                    person_x = start_x
                    person_y = start_y - offset
                    if 0 <= person_x < self._cols and 0 <= person_y < self._rows:
                        person = self._get_person(
                            person_data.sprite_path,
                            cell_w,
                            cell_h,
                        )
                        left = round(person_x * cell_w)
                        top = round(frame.height - (person_y + 1) * cell_h)
                        frame.paste(person, (left, top), person)
            if self._bikes:
                bike_cells: dict[tuple[int, int], Bike] = {}
                for bike in self._bikes:
                    key = (bike.x, bike.y)
                    existing = bike_cells.get(key)
                    if existing is None or (bike.from_left and not existing.from_left):
                        bike_cells[key] = bike
                for bike in bike_cells.values():
                    sprite = self._get_bike(bike.sprite_path, cell_w, cell_h)
                    left = round(bike.x * cell_w)
                    top = round(frame.height - (bike.y + BIKE_SIZE_CELLS) * cell_h)
                    frame.paste(sprite, (left, top), sprite)
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

    def _get_bike(self, sprite_path: Path, cell_w: float, cell_h: float) -> Image.Image:
        size = (round(BIKE_SIZE_CELLS * cell_w), round(BIKE_SIZE_CELLS * cell_h))
        key = (sprite_path, size)
        sprite = self._people_cache.get(key)
        if sprite is None:
            sprite = Image.open(sprite_path).convert("RGBA").resize(size)
            self._people_cache[key] = sprite
        return sprite

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
                if parsed.path == "/stats.json":
                    stats = runner.get_stall_stats()
                    payload = json.dumps(stats).encode("utf-8")
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
