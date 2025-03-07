#!/usr/bin/env python3
"""
Simple runner script for the TRADE system.
This allows running the application without installing it.
"""

import sys
import os
import threading

# Add the parent directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the main module directly
from TRADE import main

def show_menu():
    print("\n" + "="*40)
    print("TRADING SYSTEM MODE SELECTION".center(40))
    print("="*40)
    print("1. Live Trading Mode")
    print("2. Backtest Mode")
    print("\nAuto-starting Live Mode in 10 seconds...")
    print("="*40 + "\n")

def get_choice():
    choice = None
    
    def wait_for_input():
        nonlocal choice
        try:
            choice = input("Enter choice (1-2): ")
        except:
            pass
    
    timer = threading.Timer(10.0, lambda: None)
    timer.start()
    wait_thread = threading.Thread(target=wait_for_input)
    wait_thread.start()
    wait_thread.join(10)
    timer.cancel()
    return choice

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Direct mode execution if command line arguments provided
        main.main()
    else:
        # Interactive mode selection
        import threading
        show_menu()
        choice = get_choice()
        
        if choice == '1':
            sys.argv.append('live')
        elif choice == '2':
            sys.argv.append('backtest')
        else:
            print("\nNo valid selection made. Defaulting to Live Mode.")
            sys.argv.append('live')
        
        main.main()