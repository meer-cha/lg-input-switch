"""Entry point for the lg-daemon executable."""
import sys
sys.argv = [sys.argv[0], "daemon"]
from lg_switch import main
main()
