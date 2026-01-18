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
                        if "Session regeneration failed" in text:
                            logger.error(f"Remote update failed: {text}")
                            return False
                        
                        logger.info(f"Successfully updated remote proxy. Response: {text}")
                        self.current_proxy = proxy
                        return True
                    else:
                        logger.error(f"Failed to update remote proxy. Status: {response.status}, Response: {text}")
                        return False
        except Exception as e:
            logger.error(f"Exception during remote update: {e}")

    async def continuous_validation_loop(self):
        """Continuously re-verify all proxies in the background."""
        logger.info("Starting continuous background validation loop...")
        semaphore = asyncio.Semaphore(self.pm.concurrency_limit)
        
        while self.running:
            if not self.pm.proxies:
                await asyncio.sleep(1) # Wait if list is empty
                continue
                
            # Create a copy of the list to iterate safely
            current_proxies = list(self.pm.proxies)
            
            # We want to re-check ALL proxies continuously.
            # To avoid spamming too hard, we can process them in chunks or just all of them.
            # Let's process all of them with stats.
            
            tasks = []
            for proxy in current_proxies:
                # Reuse the check_and_add logic? No, that adds to list. 
                # We want to update existing.
                # Let's use verify_proxy_latency and update inplace.
                
                async def validate(p):
                    async with semaphore:
                         lat = await self.pm.verify_proxy_latency(p)
                         p.latency = lat
                
                tasks.append(validate(proxy))
            
            if tasks:
                await asyncio.gather(*tasks)
                
            # Re-sort after a full pass
            async with self.pm._lock:
                self.pm.proxies.sort(key=lambda x: x.latency)
                # Ensure best_proxy is updated if it degraded
                if self.pm.proxies:
                    if self.pm.best_proxy and self.pm.best_proxy.latency == float('inf'):
                         self.pm.best_proxy = self.pm.proxies[0]
                    # Or maybe just always point to top? 
                    # self.pm.best_proxy = self.pm.proxies[0] 
                    # NOTE: changing best_proxy might trigger rotation in main loop if we aren't careful.
                    # The main loop checks `current_proxy` health. 
                    # `best_proxy` is just a candidate.
            
            # Minimal sleep to yield control but keep running "non-stop"
            await asyncio.sleep(0.1)

    async def run_loop(self):
        """Main loop: Check health every 1 min, rotate only if needed."""
        self.running = True
        
        # Start the continuous background validator
        asyncio.create_task(self.continuous_validation_loop())
        
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
                while self.pm.best_proxy:
                    # Double check the new candidate
                    latency = await self.pm.verify_proxy_latency(self.pm.best_proxy)
                    self.pm.best_proxy.latency = latency
                    
                    if latency != float('inf'):
                        logger.info(f"Found new best proxy: {self.pm.best_proxy.url}. Updating remote.")
                        success = await self.update_remote(self.pm.best_proxy)
                        if success:
                            break # Success!
                        else:
                            logger.warning("Remote rejected proxy (Session regeneration failed). Trying next best.")
                            # Remove this bad proxy from consideration for now so we can process next
                            # In a real system we might blacklist it, but for now just popping from best is tricky without modifying PM state deeply
                            # Simplest: temporarily set its latency to inf so sort pushes it down, but we need to re-sort or pick next
                            # Actually, let's just mark it infinite and Resort
                            self.pm.best_proxy.latency = float('inf')
                            self.pm.proxies.sort(key=lambda x: x.latency)
                            if self.pm.proxies and self.pm.proxies[0].latency == float('inf'):
                                self.pm.best_proxy = None
                            else:
                                self.pm.best_proxy = self.pm.proxies[0]
                    else:
                        logger.warning("Proposed best proxy failed verification. Waiting for next cycle.")
                        break # If verification fails, we wait, or we could loop? Let's loop a few times?
                        # For safety, let's break and wait for next cycle to avoid infinite fast loops if all are bad
                        break
                
                if not self.pm.best_proxy:
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
            logger.info("Starting force update sequence...")
            
            while self.pm.best_proxy:
                logger.info(f"Verifying best proxy {self.pm.best_proxy.url} for force update...")
                latency = await self.pm.verify_proxy_latency(self.pm.best_proxy)
                self.pm.best_proxy.latency = latency
                
                if latency != float('inf'):
                    success = await self.update_remote(self.pm.best_proxy)
                    if success:
                        return {"status": "success", "message": f"Updated to {self.pm.best_proxy.url}", "proxy": self.pm.best_proxy.to_dict()}
                    else:
                        logger.warning("Remote rejected proxy (Session regeneration failed). Trying next best.")
                        # Demote this proxy and try next
                        self.pm.best_proxy.latency = float('inf')
                        self.pm.proxies.sort(key=lambda x: x.latency)
                        # Pick new best
                        if self.pm.proxies and self.pm.proxies[0].latency == float('inf'):
                            self.pm.best_proxy = None
                        else:
                            self.pm.best_proxy = self.pm.proxies[0]
                else:
                    logger.warning("Best proxy verification failed. Trying next best.")
                     # Demote this proxy and try next
                    self.pm.best_proxy.latency = float('inf')
                    self.pm.proxies.sort(key=lambda x: x.latency)
                    # Pick new best
                    if self.pm.proxies and self.pm.proxies[0].latency == float('inf'):
                        self.pm.best_proxy = None
                    else:
                        self.pm.best_proxy = self.pm.proxies[0]
                        
            return {"status": "error", "message": "All proxies failed verification or remote update"}
        else:
             return {"status": "error", "message": "No proxies available"}

    def stop(self):
        self.running = False
