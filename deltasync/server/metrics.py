"""Prometheus metrics, one registry per server so several can run in one process."""

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server


class Metrics:
    def __init__(self):
        self.registry = CollectorRegistry()
        r = self.registry
        self.bytes_received = Counter("deltasync_bytes_received", "Bytes read from clients", registry=r)
        self.bytes_sent = Counter("deltasync_bytes_sent", "Bytes written to clients", registry=r)
        self.literal_bytes = Counter("deltasync_literal_bytes", "New bytes carried as LITERAL ops", registry=r)
        self.matched_bytes = Counter("deltasync_matched_bytes", "Bytes reused through COPY ops", registry=r)
        self.active_transfers = Gauge("deltasync_active_transfers", "Pushes and pulls in progress", registry=r)
        self.commits = Counter("deltasync_commits", "Commits by outcome", ["outcome"], registry=r)
        self.connections = Gauge("deltasync_connections", "Open client connections", registry=r)
        self.transfer_seconds = Histogram(
            "deltasync_transfer_seconds", "Push and pull duration", ["kind"], registry=r,
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
        )

    def serve(self, port, addr="127.0.0.1"):
        return start_http_server(port, addr=addr, registry=self.registry)
