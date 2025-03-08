#!/usr/bin/env python3
"""
Trading Condition Optimizer Launcher

This script provides a simple way to launch either the command-line or GUI version
of the Trading Condition Optimizer.
"""

import os
import sys
import subprocess

def clear_screen():
    """Clear the terminal screen"""
    os.system('cls' if os.name == 'nt' else 'clear')

def show_banner():
    """Display the application banner"""
    clear_screen()
    print("\n" + "="*80)
    print("TRADING CONDITION OPTIMIZER".center(80))
    print("="*80)
    print("\nThis tool helps you find the optimal trading conditions for maximum profit.")
    print("It works with the existing TRADE system without modifying the original code.\n")

def show_menu():
    """Display the main menu"""
    print("\nPlease select an option:")
    print("1. Run Command-Line Optimizer")
    print("2. Run GUI Optimizer")
    print("3. View README")
    print("4. Exit")

def run_command_line_optimizer():
    """Run the command-line version of the optimizer"""
    clear_screen()
    print("Starting Command-Line Optimizer...\n")
    try:
        subprocess.run([sys.executable, "trade_optimizer.py"])
    except Exception as e:
        print(f"\nError running optimizer: {str(e)}")
    input("\nPress Enter to return to the menu...")

def run_gui_optimizer():
    """Run the GUI version of the optimizer"""
    clear_screen()
    print("Starting GUI Optimizer...\n")
    try:
        subprocess.Popen([sys.executable, "trade_optimizer_gui.py"])
    except Exception as e:
        print(f"\nError running GUI optimizer: {str(e)}")
        input("\nPress Enter to return to the menu...")

def view_readme():
    """Display the README file"""
    clear_screen()
    try:
        with open("optimizer_README.md", "r") as file:
            content = file.read()
        print(content)
    except Exception as e:
        print(f"\nError reading README: {str(e)}")
    input("\nPress Enter to return to the menu...")

def main():
    """Main entry point"""
    while True:
        show_banner()
        show_menu()
        
        try:
            choice = input("\nEnter your choice (1-4): ")
            
            if choice == "1":
                run_command_line_optimizer()
            elif choice == "2":
                run_gui_optimizer()
            elif choice == "3":
                view_readme()
            elif choice == "4":
                clear_screen()
                print("Thank you for using the Trading Condition Optimizer!")
                break
            else:
                print("\nInvalid choice. Please try again.")
                input("\nPress Enter to continue...")
        except KeyboardInterrupt:
            clear_screen()
            print("Exiting...")
            break
        except Exception as e:
            print(f"\nAn error occurred: {str(e)}")
            input("\nPress Enter to continue...")

if __name__ == "__main__":
    main()