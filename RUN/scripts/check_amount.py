import sys
import math
sys.path.insert(0, '/home/uns/TRADE/RUN')
from src.utils.math_utils import compute_order_amount

print(compute_order_amount(target_value_usd=1371.86, price=226.82, amount_precision=8, min_amount=0.00001, min_notional=10.0))
