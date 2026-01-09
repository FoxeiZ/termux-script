import sys
import time

print("Worker started")
sys.stdout.flush()

try:
    while True:
        print("Working...")
        sys.stdout.flush()
        time.sleep(5)
except KeyboardInterrupt:
    print("Worker stopping")
