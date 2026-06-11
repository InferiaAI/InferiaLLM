import sys
from inferia.startup_events import ServiceStarted, ServiceStarting, ServiceFailed


class StartupUI:
    def __init__(self, queue, total):
        self.queue = queue
        self.total = total
        self.started = 0
        self.done = False

    def run(self):
        while not self.done:
            event = self.queue.get()
            if isinstance(event, ServiceStarted):
                self.started += 1
                self._print_done(event.service, event.detail)
                if self.started == self.total:
                    self.done = True

            elif isinstance(event, ServiceFailed):
                self._print_fail(event.service, event.error)
                self.done = True

    def _print_done(self, name, detail):
        msg = f"✔ {name} started"
        if detail:
            msg += f" ({detail})"
        print(msg)

    def _print_fail(self, service, error):
        print(f"✖ {service} failed: {error}")
