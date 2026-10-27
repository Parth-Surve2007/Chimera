import shutil, sys
from .contracts import DiscoveryReport

def discover(names: list[str]) -> DiscoveryReport:
    return DiscoveryReport({name: shutil.which(name) for name in names}, {"python": sys.executable})

