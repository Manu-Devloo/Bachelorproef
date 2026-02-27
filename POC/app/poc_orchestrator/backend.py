"""Container backends for the PoC: real Docker and an in-memory mock."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import random
from typing import Protocol
from uuid import uuid4

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

LOGGER = logging.getLogger(__name__)


class BackendError(RuntimeError):
    """Raised when container operations fail."""


@dataclass(frozen=True)
class StartedContainer:
    container_id: str
    host_port: int


class ContainerBackend(Protocol):
    def start(
        self,
        *,
        instance_name: str,
        image: str,
        network_name: str,
        container_port: int,
        cpu_limit: float,
        memory_limit_mb: int,
        labels: dict[str, str],
    ) -> StartedContainer:
        ...

    def stop(self, *, container_id: str, network_name: str) -> None:
        ...

    def health(self) -> dict[str, str]:
        ...


class DockerBackend:
    def __init__(self) -> None:
        self.client = docker.from_env()

    def start(
        self,
        *,
        instance_name: str,
        image: str,
        network_name: str,
        container_port: int,
        cpu_limit: float,
        memory_limit_mb: int,
        labels: dict[str, str],
    ) -> StartedContainer:
        network = None
        container = None
        try:
            try:
                self.client.images.get(image)
            except ImageNotFound:
                self.client.images.pull(image)
            network = self.client.networks.create(
                name=network_name,
                driver="bridge",
                check_duplicate=True,
                labels=labels,
            )

            container = self.client.containers.run(
                image=image,
                name=instance_name,
                detach=True,
                network=network_name,
                ports={f"{container_port}/tcp": None},
                mem_limit=f"{memory_limit_mb}m",
                nano_cpus=max(int(cpu_limit * 1_000_000_000), 100_000_000),
                read_only=True,
                pids_limit=128,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
                labels=labels,
            )
            container.reload()

            port_binding = container.attrs["NetworkSettings"]["Ports"].get(
                f"{container_port}/tcp"
            )
            if not port_binding:
                raise BackendError("Container started without a published host port")

            host_port = int(port_binding[0]["HostPort"])
            return StartedContainer(container_id=container.id, host_port=host_port)
        except (APIError, DockerException, KeyError, ValueError) as exc:
            if container is not None:
                try:
                    container.remove(force=True)
                except DockerException:
                    LOGGER.exception("failed to cleanup container after startup error")
            if network is not None:
                try:
                    network.remove()
                except DockerException:
                    LOGGER.exception("failed to cleanup network after startup error")
            raise BackendError(str(exc)) from exc

    def stop(self, *, container_id: str, network_name: str) -> None:
        try:
            container = self.client.containers.get(container_id)
            container.remove(force=True)
        except NotFound:
            LOGGER.info("container %s already gone", container_id)
        except DockerException as exc:
            raise BackendError(str(exc)) from exc

        try:
            network = self.client.networks.get(network_name)
            network.remove()
        except NotFound:
            LOGGER.info("network %s already gone", network_name)
        except DockerException as exc:
            raise BackendError(str(exc)) from exc

    def health(self) -> dict[str, str]:
        try:
            self.client.ping()
            return {"backend": "docker", "status": "ok"}
        except DockerException as exc:
            return {"backend": "docker", "status": f"error: {exc}"}


class InMemoryBackend:
    """Lightweight backend for tests and dry-runs without Docker."""

    def __init__(self) -> None:
        self._containers: dict[str, dict[str, str | int]] = {}

    def start(
        self,
        *,
        instance_name: str,
        image: str,
        network_name: str,
        container_port: int,
        cpu_limit: float,
        memory_limit_mb: int,
        labels: dict[str, str],
    ) -> StartedContainer:
        container_id = f"mock-{uuid4().hex[:12]}"
        host_port = random.randint(30000, 45000)
        self._containers[container_id] = {
            "instance_name": instance_name,
            "image": image,
            "network_name": network_name,
            "container_port": container_port,
            "cpu_limit": cpu_limit,
            "memory_limit_mb": memory_limit_mb,
            "host_port": host_port,
            "labels": str(labels),
        }
        return StartedContainer(container_id=container_id, host_port=host_port)

    def stop(self, *, container_id: str, network_name: str) -> None:
        self._containers.pop(container_id, None)

    def health(self) -> dict[str, str]:
        return {"backend": "mock", "status": "ok"}
