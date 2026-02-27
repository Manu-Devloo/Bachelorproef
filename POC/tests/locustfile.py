from __future__ import annotations

import random

from locust import HttpUser, between, task


class InstanceLifecycleUser(HttpUser):
    wait_time = between(0.2, 1.0)

    @task(4)
    def start_instance(self) -> None:
        team = f"load-{random.randint(1, 5000)}"
        self.client.post(
            "/api/instances/start",
            json={"challenge_id": "demo-http", "user_id": team},
            name="start_instance",
        )

    @task(1)
    def list_instances(self) -> None:
        self.client.get("/api/instances", name="list_instances")
