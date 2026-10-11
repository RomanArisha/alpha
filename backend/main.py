import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from backend.models import MapInfo, Scanner, Tag, TagsUpdate
from backend.mqtt import mqtt_loop


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
FRONTEND_DIR = BASE_DIR.parent / "frontend"


# --------------------------------------------------
# Scanners
# --------------------------------------------------

with open(
    DATA_DIR / "scanners.json",
    "r",
    encoding="utf-8",
) as file:
    scanners = json.load(file)


# --------------------------------------------------
# Map
# --------------------------------------------------

map_info = MapInfo(
    id="main-map",
    name="Main map",
    width=20,
    height=10,
    file="/api/map/file",
)


# --------------------------------------------------
# Current tag positions
# --------------------------------------------------

tag_positions: dict[str, dict[str, float]] = {}


# --------------------------------------------------
# Application lifecycle
# --------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    mqtt_task = asyncio.create_task(
        mqtt_loop(
            scanners=scanners,
            tag_positions=tag_positions,
        )
    )

    print("MQTT background task started")

    try:
        yield

    finally:
        print("Stopping MQTT background task...")

        mqtt_task.cancel()

        try:
            await mqtt_task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)


# --------------------------------------------------
# API
# --------------------------------------------------

@app.get("/api/scanners", response_model=list[Scanner])
async def get_scanners():
    return scanners


@app.get("/api/map", response_model=MapInfo)
async def get_map():
    return map_info


@app.get("/api/map/file")
async def get_map_file():
    return FileResponse(
        DATA_DIR / "map.svg",
        media_type="image/svg+xml",
    )


# --------------------------------------------------
# WebSocket
# --------------------------------------------------

@app.websocket("/ws/tags")
async def tags_websocket(websocket: WebSocket):
    await websocket.accept()

    print("WebSocket client connected")

    try:
        while True:
            tags = [
                Tag(
                    id=tag_id,
                    position=position,
                )
                for tag_id, position in tag_positions.items()
            ]

            message = TagsUpdate(
                type="tags_update",
                timestamp=asyncio.get_running_loop().time(),
                tags=tags,
            )

            await websocket.send_json(
                message.model_dump()
            )

            await asyncio.sleep(0.2)

    except WebSocketDisconnect:
        print("WebSocket client disconnected")


# --------------------------------------------------
# Frontend
# --------------------------------------------------

@app.get("/")
async def index():
    return FileResponse(
        FRONTEND_DIR / "index.html"
    )


@app.get("/style.css")
async def style():
    return FileResponse(
        FRONTEND_DIR / "css" / "style.css"
    )


@app.get("/js/{filename}")
async def javascript(filename: str):
    return FileResponse(
        FRONTEND_DIR / "js" / filename
    )
