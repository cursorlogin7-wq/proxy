import asyncio
import logging
import uvicorn
from contextlib import asynccontextmanager
from web_server import app, init_app
from proxy_manager import ProxyManager
from updater import ProxyUpdater

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("proxy_service.log")
    ]
)
logger = logging.getLogger(__name__)

# Initialize components
pm = ProxyManager()
updater = ProxyUpdater(pm)

# Inject dependencies into web app
init_app(pm, updater)

@app.on_event("startup")
async def startup_event():
    """Start the updater loop when the application starts."""
    logger.info("Application starting...")
    asyncio.create_task(updater.run_loop())

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown."""
    logger.info("Application shutting down...")
    updater.stop()

if __name__ == "__main__":
    logger.info("Starting Proxy Rotation Service...")
    uvicorn.run(app, host="0.0.0.0", port=7860)
