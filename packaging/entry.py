"""App entry: with arguments it is the CLI, without it opens the window."""
import sys

if len(sys.argv) > 1 and not sys.argv[1].startswith("-psn"):
    from lzsync.cli import main
else:
    from lzsync.gui import main

main()
