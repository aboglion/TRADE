import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.enums import Regime
import json
import logging

logging.basicConfig(level=logging.INFO)

with open("RUN/logs/fast_forward_1y_v2.log") as f:
    for line in f:
        if "Regime:" in line:
            print(line.strip())
