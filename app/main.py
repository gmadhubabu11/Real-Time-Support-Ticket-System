from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.routers import agents, auth, categories, feedback, messages, notifications, reports, tickets
from app.websockets import endpoints as ws_endpoints

BASE_DIR = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()  # SLA check (5 min) + auto-close (daily) start with the app
    yield
    stop_scheduler()


app = FastAPI(title="Real-Time Support Ticket System", version="1.0.0", lifespan=lifespan)

for r in (auth.router, agents.router, categories.router, tickets.router, messages.router, feedback.router,
          notifications.router, reports.router, ws_endpoints.router):
    app.include_router(r)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/chat-test", include_in_schema=False)
async def chat_test():
    return FileResponse(BASE_DIR / "static" / "chat_test.html")


@app.exception_handler(IntegrityError)
async def integrity_handler(request: Request, exc: IntegrityError):
    return JSONResponse(status_code=409, content={"detail": "Conflict: duplicate or invalid reference"})


@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "docs": "/docs", "chat_test": "/chat-test"}
