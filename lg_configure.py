"""Entry point for the lg-configure executable."""
import sys
sys.argv = [sys.argv[0], "configure"]
from lg_switch import main
main()
