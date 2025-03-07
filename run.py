#!/usr/bin/env python3
"""
Simple runner script for the TRADE system.
This allows running the application without installing it.
"""

import sys
import os

# Add the parent directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the main module directly
from TRADE import main

if __name__ == "__main__":
    # Pass command line arguments to main
    main.main()