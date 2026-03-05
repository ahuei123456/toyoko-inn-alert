import logging

import uvicorn

from toyoko_inn_alert.api import app
from toyoko_inn_alert.logging_config import configure_logging

logger = logging.getLogger("toyoko.main")

if __name__ == "__main__":
    configure_logging()
    logger.info("starting_api_server host=0.0.0.0 port=8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
