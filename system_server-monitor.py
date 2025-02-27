import subprocess
import psutil


def find_process(name="CpuTracker"):
    try:
        system_server_proc = next(
            p
            for p in psutil.process_iter(attrs=["name"])
            if p.name() == "system_server"
        )
        threads = system_server_proc.threads()
        for thread in threads:
            p = psutil.Process(thread.id)
            if p.name() == name:
                return p

        return None

    except StopIteration:
        return None


def monitor_process(process: psutil.Process):
    while True:
        try:
            cpu_percent = process.cpu_percent(interval=1)
            if cpu_percent > 90:
                print(
                    f"system_server is abnormally using {cpu_percent}% CPU. Rebooting."
                )
                subprocess.run(["reboot"])

            break
        except psutil.TimeoutExpired:
            pass

        # Do something here


def main():
    process = find_process()
    if not process:
        print("Process not found")
        return

    print(f"Process found: {process}")
