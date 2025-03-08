#!/usr/bin/env python3
"""
Trading Condition Optimizer GUI

This tool provides a graphical interface for selecting historical data files
and optimizing trading conditions to find the best parameters for maximum profit.
"""

import os
import sys
import json
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from typing import Dict, List, Any

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the optimizer
from trade_optimizer import TradingOptimizer

class OptimizationThread(threading.Thread):
    """Thread for running optimization in the background"""
    
    def __init__(self, optimizer, dataset, max_combinations, callback):
        """Initialize the thread"""
        super().__init__()
        self.optimizer = optimizer
        self.dataset = dataset
        self.max_combinations = max_combinations
        self.callback = callback
        self.daemon = True  # Thread will exit when main program exits
        
    def run(self):
        """Run the optimization"""
        try:
            results = self.optimizer.optimize(self.dataset, self.max_combinations)
            self.callback(results)
        except Exception as e:
            self.callback(None, str(e))

class OptimizerGUI:
    """GUI for the Trading Condition Optimizer"""
    
    def __init__(self, root):
        """Initialize the GUI"""
        self.root = root
        self.root.title("Trading Condition Optimizer")
        self.root.geometry("900x700")
        self.root.minsize(800, 600)
        
        self.optimizer = TradingOptimizer()
        self.dataset = None
        self.results = None
        
        self.create_widgets()
        self.load_datasets()
        
    def create_widgets(self):
        """Create GUI widgets"""
        # Main frame
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Dataset selection frame
        dataset_frame = ttk.LabelFrame(main_frame, text="Dataset Selection", padding=10)
        dataset_frame.pack(fill=tk.X, pady=5)
        
        # Dataset listbox
        self.dataset_listbox = tk.Listbox(dataset_frame, height=5)
        self.dataset_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Scrollbar for dataset listbox
        scrollbar = ttk.Scrollbar(dataset_frame, orient=tk.VERTICAL, command=self.dataset_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.dataset_listbox.config(yscrollcommand=scrollbar.set)
        
        # Dataset buttons frame
        dataset_buttons_frame = ttk.Frame(main_frame, padding=(0, 5))
        dataset_buttons_frame.pack(fill=tk.X)
        
        # Refresh button
        refresh_button = ttk.Button(dataset_buttons_frame, text="Refresh Datasets", command=self.load_datasets)
        refresh_button.pack(side=tk.LEFT, padx=5)
        
        # Optimization parameters frame
        params_frame = ttk.LabelFrame(main_frame, text="Optimization Parameters", padding=10)
        params_frame.pack(fill=tk.X, pady=5)
        
        # Max combinations
        ttk.Label(params_frame, text="Maximum parameter combinations:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.max_combinations_var = tk.StringVar(value="100")
        max_combinations_entry = ttk.Entry(params_frame, textvariable=self.max_combinations_var, width=10)
        max_combinations_entry.grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        
        # Buttons frame
        buttons_frame = ttk.Frame(main_frame, padding=(0, 5))
        buttons_frame.pack(fill=tk.X)
        
        # Start optimization button
        self.optimize_button = ttk.Button(buttons_frame, text="Start Optimization", command=self.start_optimization)
        self.optimize_button.pack(side=tk.LEFT, padx=5)
        
        # Save results button
        self.save_button = ttk.Button(buttons_frame, text="Save Results", command=self.save_results, state=tk.DISABLED)
        self.save_button.pack(side=tk.LEFT, padx=5)
        
        # Apply parameters button
        self.apply_button = ttk.Button(buttons_frame, text="Apply Best Parameters", command=self.apply_parameters, state=tk.DISABLED)
        self.apply_button.pack(side=tk.LEFT, padx=5)
        
        # Progress frame
        progress_frame = ttk.Frame(main_frame, padding=(0, 5))
        progress_frame.pack(fill=tk.X, pady=5)
        
        # Progress bar
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X)
        
        # Status label
        self.status_var = tk.StringVar(value="Ready")
        status_label = ttk.Label(progress_frame, textvariable=self.status_var)
        status_label.pack(anchor=tk.W, pady=5)
        
        # Results frame
        results_frame = ttk.LabelFrame(main_frame, text="Optimization Results", padding=10)
        results_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Results text
        self.results_text = tk.Text(results_frame, wrap=tk.WORD, height=20)
        self.results_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Scrollbar for results text
        results_scrollbar = ttk.Scrollbar(results_frame, orient=tk.VERTICAL, command=self.results_text.yview)
        results_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.results_text.config(yscrollcommand=results_scrollbar.set)
        
    def load_datasets(self):
        """Load available datasets"""
        self.dataset_listbox.delete(0, tk.END)
        datasets = self.optimizer.data_storage.get_available_datasets()
        
        if not datasets:
            self.status_var.set("No datasets found in data/ directory")
            return
            
        for dataset in datasets:
            self.dataset_listbox.insert(tk.END, dataset)
            
        self.status_var.set(f"Loaded {len(datasets)} datasets")
        
    def start_optimization(self):
        """Start the optimization process"""
        # Get selected dataset
        selection = self.dataset_listbox.curselection()
        if not selection:
            messagebox.showerror("Error", "Please select a dataset")
            return
            
        dataset = self.dataset_listbox.get(selection[0])
        self.dataset = dataset
        
        # Get max combinations
        try:
            max_combinations = int(self.max_combinations_var.get())
            if max_combinations <= 0:
                raise ValueError("Maximum combinations must be positive")
        except ValueError as e:
            messagebox.showerror("Error", str(e))
            return
            
        # Disable buttons
        self.optimize_button.config(state=tk.DISABLED)
        self.save_button.config(state=tk.DISABLED)
        self.apply_button.config(state=tk.DISABLED)
        
        # Clear results
        self.results_text.delete(1.0, tk.END)
        self.results = None
        
        # Update status
        self.status_var.set(f"Optimizing trading conditions for {dataset}...")
        
        # Start optimization thread
        thread = OptimizationThread(
            self.optimizer,
            dataset,
            max_combinations,
            self.optimization_complete
        )
        thread.start()
        
        # Start progress update
        self.update_progress()
        
    def update_progress(self):
        """Update progress bar"""
        if hasattr(self.optimizer, 'progress'):
            self.progress_var.set(self.optimizer.progress * 100)
        
        # Check if optimization is still running
        if self.optimize_button['state'] == tk.DISABLED and not self.results:
            self.root.after(100, self.update_progress)
        
    def optimization_complete(self, results, error=None):
        """Handle optimization completion"""
        if error:
            self.status_var.set(f"Error: {error}")
            messagebox.showerror("Optimization Error", error)
        else:
            self.results = results
            self.status_var.set("Optimization complete!")
            self.display_results()
            self.save_button.config(state=tk.NORMAL)
            self.apply_button.config(state=tk.NORMAL)
            
        self.optimize_button.config(state=tk.NORMAL)
        self.progress_var.set(100)
        
    def display_results(self):
        """Display optimization results"""
        if not self.results:
            return
            
        self.results_text.delete(1.0, tk.END)
        
        self.results_text.insert(tk.END, "OPTIMIZATION RESULTS\n")
        self.results_text.insert(tk.END, "="*50 + "\n\n")
        
        for i, result in enumerate(self.results[:5], 1):
            params = result['parameters']
            perf = result['performance']
            
            self.results_text.insert(tk.END, f"Rank #{i}:\n")
            self.results_text.insert(tk.END, f"  Win Rate: {perf.get('win_rate', 0)*100:.2f}%\n")
            self.results_text.insert(tk.END, f"  Profit Factor: {perf.get('profit_factor', 0):.2f}\n")
            self.results_text.insert(tk.END, f"  Total Trades: {perf.get('total_trades', 0)}\n")
            self.results_text.insert(tk.END, f"  Avg Win: {perf.get('avg_win', 0):.2f}%\n")
            self.results_text.insert(tk.END, f"  Avg Loss: {perf.get('avg_loss', 0):.2f}%\n")
            self.results_text.insert(tk.END, f"  Max Drawdown: {perf.get('max_drawdown', 0):.2f}%\n")
            self.results_text.insert(tk.END, f"  Sharpe Ratio: {perf.get('sharpe_ratio', 0):.2f}\n\n")
            
            self.results_text.insert(tk.END, "  BUY CONDITIONS:\n")
            for key, value in params['BUY'].items():
                self.results_text.insert(tk.END, f"    {key}: {value}\n")
                
            self.results_text.insert(tk.END, "\n  EXIT CONDITIONS:\n")
            for key, value in params['EXIT'].items():
                self.results_text.insert(tk.END, f"    {key}: {value}\n")
                
            self.results_text.insert(tk.END, "\n" + "-"*50 + "\n\n")
            
    def save_results(self):
        """Save optimization results to a file"""
        if not self.results:
            messagebox.showerror("Error", "No optimization results available")
            return
            
        # Get filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        default_filename = f"optimization_results_{timestamp}.json"
        
        filename = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile=default_filename
        )
        
        if not filename:
            return
            
        try:
            with open(filename, 'w', encoding='utf-8') as file:
                json.dump(self.results, file, indent=2, default=str)
            self.status_var.set(f"Results saved to {filename}")
            messagebox.showinfo("Success", f"Results saved to {filename}")
        except Exception as e:
            messagebox.showerror("Error", f"Error saving results: {str(e)}")
            
    def apply_parameters(self):
        """Apply optimized parameters to the trading configuration"""
        if not self.results:
            messagebox.showerror("Error", "No optimization results available")
            return
            
        # Get confirmation
        confirm = messagebox.askyesno(
            "Confirm",
            "Are you sure you want to apply the best parameters to your trading configuration?"
        )
        
        if not confirm:
            return
            
        try:
            # Apply parameters
            self.optimizer.apply_parameters(self.results[0]['parameters'])
            self.status_var.set("Optimized parameters applied and saved")
            messagebox.showinfo("Success", "Optimized parameters applied and saved")
        except Exception as e:
            messagebox.showerror("Error", f"Error applying parameters: {str(e)}")

def main():
    """Main entry point for the GUI"""
    root = tk.Tk()
    app = OptimizerGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()