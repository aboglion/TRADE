"""
Strategy interface.

Defines the contract that all strategy implementations must follow.
Re-exports IStrategy from interfaces for convenience.
"""

from src.core.interfaces import IStrategy

__all__ = ["IStrategy"]
