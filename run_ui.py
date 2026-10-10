import os
import uvicorn
from frontend.app import app


if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT", "5000"))
    uvicorn.run("frontend.app:app", host=host, port=port, reload=True)
