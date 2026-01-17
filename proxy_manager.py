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

    def to_dict(self):
        return asdict(self)

class ProxyManager:
    def __init__(self):
        self.proxies: List[Proxy] = [] # This will hold ONLY working proxies
        self.best_proxy: Optional[Proxy] = None
        self.test_target = "http://www.google.com" 
        self.timeout = 5 # seconds
        self.concurrency_limit = 20 # Higher concurrency
        self._lock = asyncio.Lock() # Lock for thread-safe updates to self.proxies

    async def fetch_json_proxies(self) -> List[Proxy]:
        """Fetch proxies from proxies.free JSON source."""
        url = f"https://proxies.free/data/proxies.json?t={int(time.time())}"
        fetched = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        for p in data.get('proxies', []):
                            # Filter for http/https/socks5 only
                            protocol = p.get('protocol', '').lower()
                            if protocol in ['http', 'https', 'socks5']:
                                proxy_url = f"{protocol}://{p['ip']}:{p['port']}"
                                fetched.append(Proxy(
                                    url=proxy_url,
                                    protocol=protocol,
                                    ip=p['ip'],
                                    port=p['port']
                                ))
        except Exception as e:
            logger.error(f"Error fetching JSON proxies: {e}")
        return fetched

    async def fetch_txt_proxies(self) -> List[Proxy]:
        """Fetch proxies from proxifly TXT source."""
        url = "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/all/data.txt"
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
                                protocol, rest = line.split('://')
                                ip, port = rest.split(':')
                                if protocol.lower() in ['http', 'https', 'socks5']:
                                    fetched.append(Proxy(
                                        url=line,
                                        protocol=protocol.lower(),
                                        ip=ip,
                                        port=port
                                    ))
                            except ValueError:
                                continue
        except Exception as e:
            logger.error(f"Error fetching TXT proxies: {e}")
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
        p1 = await self.fetch_json_proxies()
        p2 = await self.fetch_txt_proxies()
        
        # Deduplicate
        seen = set()
        unique_proxies = []
        for p in p1 + p2:
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
