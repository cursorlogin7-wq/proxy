from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import uvicorn
from proxy_manager import ProxyManager
from updater import ProxyUpdater
import logging

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# These will be injected from main.py
proxy_manager: ProxyManager = None
updater: ProxyUpdater = None

def init_app(pm: ProxyManager, up: ProxyUpdater):
    global proxy_manager, updater
    proxy_manager = pm
    updater = up

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/status")
async def get_status():
    if not proxy_manager:
        return {"best_proxy": None, "total_available": 0}
    
    best = proxy_manager.best_proxy.to_dict() if proxy_manager.best_proxy else None
    return {
        "best_proxy": best,
        "total_available": len(proxy_manager.proxies)
    }

@app.get("/api/proxies")
async def get_proxies():
    if not proxy_manager:
        return {"proxies": []}
    # Return top 50 proxies
    return {"proxies": [p.to_dict() for p in proxy_manager.proxies[:50]]}

@app.get("/api/force")
async def force_update_proxy():
    if not updater:
        return {"status": "error", "message": "Updater not initialized"}
    result = await updater.force_update()
    return result
