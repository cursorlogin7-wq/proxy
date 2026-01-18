import aiohttp
import asyncio
import time
import logging
from typing import List, Optional, Dict
from dataclasses import dataclass, asdict

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class Proxy:
    url: str
    protocol: str
    ip: str
    port: str
    latency: float = float('inf')
    source: str = "unknown"

    def to_dict(self):
        return asdict(self)

class ProxyManager:
    def __init__(self):
        self.proxies: List[Proxy] = [] # This will hold ONLY working proxies
        self.best_proxy: Optional[Proxy] = None
        self.test_target = "https://www.youtube.com/watch?v=2FE7pOBbRn8" 
        self.timeout = 10 # seconds
        self.concurrency_limit = 50 # Higher concurrency
        self._lock = asyncio.Lock() # Lock for thread-safe updates to self.proxies



    async def fetch_socks5_github(self) -> List[Proxy]:
        """Fetch SOCKS5 proxies from TheSpeedX GitHub."""
        url = "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt"
        fetched = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        text = await response.text()
                        for line in text.splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                ip, port = line.split(':')
                                fetched.append(Proxy(
                                    url=f"socks5://{ip}:{port}",
                                    protocol="socks5",
                                    ip=ip,
                                    port=port,
                                    source="github_socks5"
                                ))
                            except ValueError:
                                continue
        except Exception as e:
            logger.error(f"Error fetching GitHub SOCKS5 proxies: {e}")
        return fetched

    async def fetch_http_github(self) -> List[Proxy]:
        """Fetch HTTP proxies from TheSpeedX GitHub."""
        url = "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt"
        fetched = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        text = await response.text()
                        for line in text.splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                ip, port = line.split(':')
                                fetched.append(Proxy(
                                    url=f"http://{ip}:{port}",
                                    protocol="http",
                                    ip=ip,
                                    port=port,
                                    source="github_http"
                                ))
                            except ValueError:
                                continue
        except Exception as e:
            logger.error(f"Error fetching GitHub HTTP proxies: {e}")
        return fetched

    async def check_and_add_proxy(self, proxy: Proxy, semaphore: asyncio.Semaphore):
        """Test a proxy and add it to the valid list immediately if successful."""
        async with semaphore:
            start_time = time.time()
            try:
                # Use a new session for each check to avoid connection pool limits/issues with proxies
                timeout = aiohttp.ClientTimeout(total=self.timeout)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(self.test_target, proxy=proxy.url) as response:
                        if response.status == 200:
                            proxy.latency = (time.time() - start_time) * 1000 # ms
                            
                            # Add to working list immediately
                            async with self._lock:
                                self.proxies.append(proxy)
                                self.proxies.sort(key=lambda x: x.latency)
                                self.best_proxy = self.proxies[0]
                                
            except Exception:
                pass # Check failed, just ignore

    async def verify_proxy_latency(self, proxy: Proxy) -> float:
        """Verify a single proxy's latency. Returns float('inf') if failed."""
        start_time = time.time()
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(self.test_target, proxy=proxy.url) as response:
                    if response.status == 200:
                        return (time.time() - start_time) * 1000
        except Exception:
            pass
        return float('inf')

    async def refresh_proxies(self) -> List[Proxy]:
        """Fetch and test all proxies."""
        logger.info("Fetching proxies...")
        # Only use GitHub sources
        p1 = await self.fetch_socks5_github()
        p2 = await self.fetch_http_github()
        
        # Deduplicate and Balance
        def dedupe(proxies):
            seen = set()
            out = []
            for p in proxies:
                if p.url not in seen:
                    seen.add(p.url)
                    out.append(p)
            return out

        p1 = dedupe(p1)
        p2 = dedupe(p2)
        
        # Balance: Take equal number from each source
        counts = [len(p1), len(p2)]
        non_zero_counts = [c for c in counts if c > 0]
        
        if not non_zero_counts:
             min_count = 0
        else:
             min_count = min(non_zero_counts)

        logger.info(f"Balancing proxies: GH_SOCKS5={len(p1)}, GH_HTTP={len(p2)}. Taking {min_count} from each available.")
        
        def slice_list(lst, count):
             return lst[:count] if lst else []

        balanced_proxies = slice_list(p1, min_count) + slice_list(p2, min_count)
        
        # Global deduplication
        seen = set()
        unique_proxies = []
        for p in balanced_proxies:
            if p.url not in seen:
                seen.add(p.url)
                unique_proxies.append(p)
        
        logger.info(f"Testing {len(unique_proxies)} proxies with concurrency {self.concurrency_limit}...")
        
        # Reset current list before refresh? 
        # Ideally, we keep old ones until new ones replace, but to keep it simple and stateless:
        # We will clear it. UI might flicker but updates will come fast.
        async with self._lock:
            self.proxies = []
            self.best_proxy = None

        semaphore = asyncio.Semaphore(self.concurrency_limit)
        tasks = [self.check_and_add_proxy(p, semaphore) for p in unique_proxies]
        
        # Run all checks
        await asyncio.gather(*tasks)
        
        logger.info(f"Refresh complete. Found {len(self.proxies)} working proxies.")
        return self.proxies
