"""
Entry point: the original FRP series route, the calendar routes, the world
routes and the web pages.
  /          the globe (landing page)
  /calendar  the full calendar page
Run with:  python -m uvicorn app.server:app --reload
"""
from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.calendar_api import router as calendar_router
from app.main import app
from app.world_api import router as world_router

STATIC = Path(__file__).resolve().parent.parent / "static"

app.include_router(calendar_router)
app.include_router(world_router)


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC / "globe.html")


@app.get("/calendar", include_in_schema=False)
def calendar_page():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
