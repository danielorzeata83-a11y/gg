import sys
import os

# Add parent directory to sys.path so tests can import project modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
