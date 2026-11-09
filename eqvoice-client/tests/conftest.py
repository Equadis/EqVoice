import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, HERE)
# The signalling package is a sibling project; the client tests drive a real hub.
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "eqvoice-signalling")))
