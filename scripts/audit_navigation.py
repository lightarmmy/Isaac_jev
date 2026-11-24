"""Project entry for phase-aware navigation audit."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from isaac_jev.navigation_audit import main

if __name__=="__main__":main()
