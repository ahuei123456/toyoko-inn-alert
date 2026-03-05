import uvicorn

from toyoko_inn_alert.api import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
