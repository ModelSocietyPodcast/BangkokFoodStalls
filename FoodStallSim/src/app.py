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

from model import (
    FrameRunner,
    REAL_SECONDS_PER_STEP,
    SIM_MINUTES_PER_STEP,
    load_dish_catalog,
    load_stall_names,
)

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
poll_seconds = max(0.5, REAL_SECONDS_PER_STEP / 2)
stats_refresh_seconds = max(
    5.0,
    (60 / SIM_MINUTES_PER_STEP) * REAL_SECONDS_PER_STEP,
)

with col_right:
    st.markdown('<div class="grid-anchor"></div>', unsafe_allow_html=True)
    st.markdown('<div class="grid-wrap">', unsafe_allow_html=True)
    frame_container = st.empty()
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
    sim_time_container = st.empty()

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
        audio_container = st.empty()
        audio_sync_container = st.empty()
        if audio_data_uri:
            with audio_container:
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
                    const key = "street-audio-enabled";
                    enable.addEventListener("click", () => {{
                        localStorage.setItem(key, "true");
                        audio.volume = 0.5;
                        audio.play().catch(() => {{}});
                    }});
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
            padding-top: 1.25rem;
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

    stats_title_container = st.empty()
    stats_container = st.empty()
    if "last_stats_hour" not in st.session_state:
        st.session_state.last_stats_hour = None
        st.session_state.last_stats_rows = []
        st.session_state.last_stats_label = "### Food Stall Stats (last updated at --:--)"
    if "last_clock_running" not in st.session_state:
        st.session_state.last_clock_running = None


@st.fragment(run_every=poll_seconds)
def refresh_frame_and_time() -> None:
    state = runner.get_state()
    sim_time_container.markdown(f"Sim time: {state['sim_time']}")
    frame_container.image(
        runner.get_frame_bytes(),
        width=display_width,
    )
    if audio_data_uri:
        with audio_sync_container:
            html(
                f"""
                <script>
                const audio = document.getElementById("street-audio");
                if (audio) {{
                    const enabled = localStorage.getItem("street-audio-enabled") === "true";
                    if ({str(state["clock_running"]).lower()}) {{
                        if (enabled && audio.paused) {{
                            audio.play().catch(() => {{}});
                        }}
                    }} else {{
                        audio.pause();
                        audio.currentTime = 0;
                    }}
                }}
                </script>
                """,
                height=0,
                width=0,
            )


@st.fragment(run_every=stats_refresh_seconds)
def refresh_stats() -> None:
    state = runner.get_state()
    current_hour = state["sim_minutes"] // 60
    last_hour = st.session_state.last_stats_hour
    if last_hour is None or current_hour > last_hour:
        stats_rows = []
        for row in runner.get_stall_stats():
            stats_rows.append(
                {
                    "Stall": row["stall_name"],
                    "Specialty": row["specialty"],
                    "Price (baht)": row["price"],
                    "Revenue (baht)": row["revenue"],
                    "Avg Service (min)": row["avg_service_time"],
                    "Rating": row["rating_display"],
                }
            )
        st.session_state.last_stats_hour = current_hour
        st.session_state.last_stats_rows = stats_rows
        st.session_state.last_stats_label = (
            f"### Food Stall Stats (last updated at {current_hour:02d}:00)"
        )
    stats_title_container.markdown(st.session_state.last_stats_label)
    if st.session_state.last_stats_rows:
        stats_container.dataframe(
            st.session_state.last_stats_rows,
            width="stretch",
            hide_index=True,
            height=260,
        )
    else:
        stats_container.caption("Loading stats...")
    stats_title_container.markdown(st.session_state.last_stats_label)
    if st.session_state.last_stats_rows:
        stats_container.dataframe(
            st.session_state.last_stats_rows,
            use_container_width=True,
            hide_index=True,
            height=260,
        )
    else:
        stats_container.caption("Loading stats...")


refresh_frame_and_time()
refresh_stats()

