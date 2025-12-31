"""
Streamlit stage preview with discrete queue simulation:
- Builds a Mesa model with a 20x22 grid
- Composites sky + stage into a single image layer
- Advances time in 6-minute steps and updates queue sprites

Run:
  pip install mesa streamlit pillow
  streamlit run st_stage_preview.py
"""

from __future__ import annotations

from pathlib import Path
import base64

import streamlit as st
from streamlit.components.v1 import html

import mesa
from mesa.space import MultiGrid

from model import FrameRunner, REAL_SECONDS_PER_STEP, load_dish_catalog, load_stall_names

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
AUDIO_PATH = Path(__file__).resolve().parents[1] / "assets" / "audio" / "Street_Sounds.m4a"

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

def load_audio_data_uri(path: Path) -> str | None:
    if not path.exists():
        return None
    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:audio/mp4;base64,{encoded}"

audio_data_uri = load_audio_data_uri(AUDIO_PATH)

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
        ""
        "Some stalls are more organized than others, and some dishes are more expensive. "
        "Therefore, some stalls serve more customers and/or may earn more revenue over time! "
        "You can see this in the stats table at the bottom left. You can also click the 'Enable "
        "street audio' button to hear ambient sounds from the street as the simulation runs."
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

    if "show_about" not in st.session_state:
        st.session_state.show_about = True

    def toggle_about() -> None:
        st.session_state.show_about = not st.session_state.show_about
    col_start, col_stop, col_toggle, col_audio = st.columns(4)
    with col_start:
        if st.button("Start Clock"):
            runner.start()
    with col_stop:
        if st.button("Stop Clock"):
            runner.stop()
    with col_toggle:
        label = "Hide About" if st.session_state.show_about else "Show About"
        st.button(label, key="toggle_about", on_click=toggle_about)
    with col_audio:
        if audio_data_uri:
            html(
                f"""
                <style>
                .audio-controls {{
                    margin: 0;
                }}
                .audio-controls button {{
                    background: #111111;
                    color: #ffffff;
                    border: none;
                    border-radius: 999px;
                    padding: 0.35rem 0.8rem;
                    font-size: 0.85rem;
                    cursor: pointer;
                }}
                </style>
                <div class="audio-controls">
                    <audio id="street-audio" loop preload="auto" src="{audio_data_uri}"></audio>
                    <button id="audio-enable" type="button">Enable street audio</button>
                </div>
                <script>
                const audio = document.getElementById("street-audio");
                const enable = document.getElementById("audio-enable");
                const stateUrl = "http://127.0.0.1:{runner.port}/state.json";
                let userEnabled = false;

                enable.addEventListener("click", () => {{
                    userEnabled = true;
                    audio.volume = 0.5;
                    audio.play().catch(() => {{}});
                }});

                async function syncAudio() {{
                    try {{
                        const resp = await fetch(stateUrl, {{ cache: "no-store" }});
                        const data = await resp.json();
                        if (!userEnabled) {{
                            return;
                        }}
                        if (data.clock_running) {{
                            if (audio.paused) {{
                                audio.play().catch(() => {{}});
                            }}
                        }} else if (!audio.paused) {{
                            audio.pause();
                            audio.currentTime = 0;
                        }}
                    }} catch (err) {{
                        // Ignore transient fetch errors.
                    }}
                }}
                syncAudio();
                setInterval(syncAudio, {poll_ms});
                </script>
                """,
                height=64,
            )
    bike_rate = st.slider(
        "Motorbike entry rate (%)",
        min_value=0.0,
        max_value=10.0,
        value=1.0,
        step=0.5,
    )
    runner.set_bike_spawn_chance(bike_rate / 100.0)

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

    if st.session_state.show_about:
        st.markdown("### About")
        st.text_area(
            "Simulation message",
            value=(
                "Hungry customers line up in front of their favorite stalls. Their "
                "preferences vary - some are in the mood for something savory, while "
                "others may be determined to indulge a sweet tooth. They might also be "
                "more or less frugal with their money and/or time. Stalls, similarly, "
                "are heterogenous - some are ramshackle establishments offering authentic "
                "Thai delicacies for next to nothing, while others are sleek, shiny, and "
                "costly. Once customers receive their food, money changes hands, and they "
                "rate the service they've received. You can see the amount of revenue "
                "each stall collects over the course of the day and average service "
                "times and ratings in the 'Food Stall Stats' table below!"
            ),
            height=200,
            disabled=True,
            label_visibility="collapsed",
        )

    st.markdown("### Food Stall Stats")
    html(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&display=swap');
        table.stats {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.88rem;
            font-family: "IBM Plex Sans", "Source Sans Pro", sans-serif;
        }}
        table.stats th,
        table.stats td {{
            border: 1px solid #d0d0d0;
            padding: 6px 8px;
            text-align: left;
            white-space: normal;
            overflow-wrap: anywhere;
        }}
        table.stats th {{
            background: #f4c2c2;
        }}
        .stats-scroll {{
            max-height: 240px;
            overflow-y: auto;
        }}
        </style>
        <div id="stall-stats" class="stats-scroll">Loading stats...</div>
        <script>
        const statsUrl = "http://127.0.0.1:{runner.port}/stats.json";
        const container = document.getElementById("stall-stats");
        function renderStats(rows) {{
            let html = "<table class='stats'><thead><tr>";
            html += "<th>Stall</th>";
            html += "<th>Specialty</th>";
            html += "<th>Price</th>";
            html += "<th>Revenue</th>";
            html += "<th>Avg Service (min)</th>";
            html += "<th>Rating</th>";
            html += "</tr></thead><tbody>";
            for (const row of rows) {{
                html += "<tr>";
                html += `<td>${{row.stall_name}}</td>`;
                html += `<td>${{row.specialty}}</td>`;
                html += `<td>${{row.price}} baht</td>`;
                html += `<td>${{row.revenue}} baht</td>`;
                html += `<td>${{row.avg_service_time}} min</td>`;
                html += `<td>${{row.rating_display}}</td>`;
                html += "</tr>";
            }}
            html += "</tbody></table>";
            container.innerHTML = html;
        }}
        async function refreshStats() {{
            try {{
                const resp = await fetch(statsUrl, {{ cache: "no-store" }});
                const rows = await resp.json();
                renderStats(rows);
            }} catch (err) {{
                // Ignore transient fetch errors.
            }}
        }}
        refreshStats();
        setInterval(refreshStats, {poll_ms});
        </script>
        """,
        height=300,
    )

