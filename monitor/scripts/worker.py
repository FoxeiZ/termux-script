import os
import sys
import time

print("Worker started")
sys.stdout.flush()

try:
    while True:
        if os.name == "nt":
            print(f"i am worker with pid:{os.getpid()}")
        else:
            print(f"i am worker with pid:{os.getpid()} uid:{os.getuid()} gid:{os.getgid()}")
        sys.stdout.flush()
        time.sleep(5)
except KeyboardInterrupt:
    print("Worker stopping")
    sys.stdout.flush()
