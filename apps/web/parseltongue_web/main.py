import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

app = FastAPI(
    title="Parseltongue",
    description="Browse and listen to fan fiction stories",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


def start() -> None:
    uvicorn.run("parseltongue_web.main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    start()
