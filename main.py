from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from database import engine
import models
from routers import api, pages


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup
    models.Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Department Selection App", lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include routers
app.include_router(api.router, prefix="/api", tags=["api"])
app.include_router(pages.router, tags=["pages"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
