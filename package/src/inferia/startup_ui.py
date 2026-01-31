# startup_ui.py
import sys
import time
import threading
from inferia.startup_events import ServiceStarted, ServiceStarting, ServiceFailed

SPINNER = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]

class StartupUI:
    def __init__(self, queue, total):
        self.queue = queue
        self.total = total
        self.started = 0
        self.current = "initializing"
        self.done = False

    def run(self):
        spinner_thread = threading.Thread(target=self._spinner, daemon=True)
        spinner_thread.start()

        while not self.done:
            event = self.queue.get()
            if isinstance(event, ServiceStarting):
                self.current = event.service

            elif isinstance(event, ServiceStarted):
                self.started += 1
                self._print_done(event.service, event.detail)
                if self.started == self.total:
                    self.done = True

            elif isinstance(event, ServiceFailed):
                self._print_fail(event.service, event.error)
                self.done = True

        sys.stdout.write("\r" + " " * 80 + "\r")
        sys.stdout.flush()

    def _spinner(self):
        i = 0
        while not self.done:
            sys.stdout.write(f"\r{SPINNER[i % len(SPINNER)]} Starting {self.current}...")
            sys.stdout.flush()
            i += 1
            time.sleep(0.1)

    def _print_done(self, name, detail):
        sys.stdout.write("\r" + " " * 80 + "\r")
        msg = f"✔ {name} started"
        if detail:
            msg += f" ({detail})"
        print(msg)

    def _print_fail(self, service, error):
        sys.stdout.write("\r" + " " * 80 + "\r")
        print(f"✖ {service} failed: {error}")
