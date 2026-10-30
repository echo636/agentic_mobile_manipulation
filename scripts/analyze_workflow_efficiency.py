#!/usr/bin/env python3
"""Report workflow costs and goal-note replacements from an existing episode."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.reporting.workflow_efficiency import main

if __name__ == '__main__':
    main()
