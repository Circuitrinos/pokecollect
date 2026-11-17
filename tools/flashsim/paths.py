"""Where the simulator finds Brian's footage (DATA) and keeps what it makes (WORK)."""
import os
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("FLASHSIM_DATA", os.path.join(HERE, "data"))
WORK = os.environ.get("FLASHSIM_WORK", os.path.join(HERE, "work"))
os.makedirs(WORK, exist_ok=True)
data = lambda *p: os.path.join(DATA, *p)
work = lambda *p: os.path.join(WORK, *p)
