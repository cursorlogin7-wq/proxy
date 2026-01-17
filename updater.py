import asyncio
import logging
import urllib.parse
import aiohttp
from proxy_manager import ProxyManager, Proxy

logger = logging.getLogger(__name__)

class ProxyUpdater:
    def __init__(self, proxy_manager: ProxyManager):
        self.pm = proxy_manager
        self.update_url_template = "https://sasasas.zeabur.app/api/set/proxy/{}"
        self.running = False

    async def update_remote(self, proxy: Proxy):
        """Update the remote instance with the new proxy."""
        # URL encode the proxy URL
        encoded_proxy = urllib.parse.quote(proxy.url, safe='')
        target_url = self.update_url_template.format(encoded_proxy)
        
        logger.info(f"Updating remote instance with proxy: {proxy.url}")
        logger.info(f"Target URL: {target_url}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(target_url) as response:
                    text = await response.text()
                    if response.status == 200:
                        logger.info(f"Successfully updated remote proxy. Response: {text}")
                    else:
                        logger.error(f"Failed to update remote proxy. Status: {response.status}, Response: {text}")
        except Exception as e:
            logger.error(f"Exception during remote update: {e}")

    async def run_loop(self):
        """Main loop ensuring proxy is updated every 5 minutes."""
        self.running = True
        while self.running:
            logger.info("Starting scheduled proxy update...")
            
            # 1. Refresh and find best proxy
            proxies = await self.pm.refresh_proxies()
            
            if self.pm.best_proxy:
                # 2. Test one last time before updating
                latency = await self.pm.verify_proxy_latency(self.pm.best_proxy)
                self.pm.best_proxy.latency = latency
                
                if latency != float('inf'):
                    # 3. Update remote
                    await self.update_remote(self.pm.best_proxy)
                else:
                    logger.warning("Best proxy failed verification. Retrying in next cycle.")
            else:
                logger.warning("No valid proxies found to update.")
            
            logger.info("Sleeping for 5 minutes...")
            await asyncio.sleep(300) # 5 minutes

    def stop(self):
        self.running = False
