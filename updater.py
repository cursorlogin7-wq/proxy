import asyncio
import logging
import urllib.parse
import time
import aiohttp
from proxy_manager import ProxyManager, Proxy

logger = logging.getLogger(__name__)

class ProxyUpdater:
    def __init__(self, proxy_manager: ProxyManager):
        self.pm = proxy_manager
        self.update_url_template = "https://sasasas.zeabur.app/api/set/proxy/{}"
        self.running = False
        self.current_proxy: Proxy = None
        self.last_full_refresh = 0

    async def update_remote(self, proxy: Proxy):
        """Update the remote instance with the new proxy."""
        # URL encode the proxy URL
        encoded_proxy = urllib.parse.quote(proxy.url, safe='')
        target_url = self.update_url_template.format(encoded_proxy)
        
        logger.info(f"Updating remote instance with proxy: {proxy.url}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(target_url) as response:
                    text = await response.text()
                    if response.status == 200:
                        logger.info(f"Successfully updated remote proxy. Response: {text}")
                        self.current_proxy = proxy
                    else:
                        logger.error(f"Failed to update remote proxy. Status: {response.status}, Response: {text}")
        except Exception as e:
            logger.error(f"Exception during remote update: {e}")

    async def run_loop(self):
        """Main loop: Check health every 1 min, rotate only if needed."""
        self.running = True
        while self.running:
            logger.info("Executing maintenance cycle (10-second interval)...")
            now = time.time()
            
            # 1. Health Check Current Proxy
            current_is_healthy = False
            if self.current_proxy:
                logger.info(f"Checking health of current proxy: {self.current_proxy.url}")
                latency = await self.pm.verify_proxy_latency(self.current_proxy)
                
                if latency != float('inf'):
                    logger.info(f"Current proxy is HEALTHY ({latency:.2f}ms). Keeping it.")
                    self.current_proxy.latency = latency
                    current_is_healthy = True
                else:
                    logger.warning("Current proxy DIED. Initiating immediate rotation.")
                    self.current_proxy = None # Mark as dead
            
            # 2. Refresh List (Background constraint: Every 5 mins OR if we need a new proxy)
            # If we need a new proxy (not healthy), we MUST refresh/find best.
            # If we are healthy, we only refresh if > 5 mins have passed (to keep backup list fresh).
            needs_new_proxy = not current_is_healthy
            time_since_refresh = now - self.last_full_refresh
            
            if needs_new_proxy or time_since_refresh > 300:
                logger.info("Refreshing proxy list...")
                # Note: This updates pm.proxies and pm.best_proxy
                await self.pm.refresh_proxies() 
                self.last_full_refresh = time.time()
            
            # 3. Rotate if needed
            if needs_new_proxy:
                if self.pm.best_proxy:
                    # Double check the new candidate
                    latency = await self.pm.verify_proxy_latency(self.pm.best_proxy)
                    self.pm.best_proxy.latency = latency
                    
                    if latency != float('inf'):
                        logger.info(f"Found new best proxy: {self.pm.best_proxy.url}. Updating remote.")
                        await self.update_remote(self.pm.best_proxy)
                    else:
                        logger.warning("Proposed best proxy failed verification. Waiting for next cycle.")
                else:
                    logger.warning("No working proxies available to switch to.")
            
            # Sleep 10 seconds (10s update interval)
            logger.info("Sleeping for 10 seconds...")
            await asyncio.sleep(10)

    async def force_update(self):
        """Force an immediate update to the best available proxy."""
        logger.info("Force update requested.")
        
        if not self.pm.best_proxy:
            # Try to refresh if no proxy is known
            await self.pm.refresh_proxies()

        if self.pm.best_proxy:
            logger.info(f"Verifying best proxy {self.pm.best_proxy.url} for force update...")
            latency = await self.pm.verify_proxy_latency(self.pm.best_proxy)
            self.pm.best_proxy.latency = latency
            
            if latency != float('inf'):
                await self.update_remote(self.pm.best_proxy)
                return {"status": "success", "message": f"Updated to {self.pm.best_proxy.url}", "proxy": self.pm.best_proxy.to_dict()}
            else:
                return {"status": "error", "message": "Best proxy failed verification"}
        else:
             return {"status": "error", "message": "No proxies available"}

    def stop(self):
        self.running = False
