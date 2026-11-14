#!/usr/bin/env python3
"""Print the six efficiency metrics of model episodes and references as one table."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.reporting.efficiency_table import main

if __name__ == '__main__':
    main()
