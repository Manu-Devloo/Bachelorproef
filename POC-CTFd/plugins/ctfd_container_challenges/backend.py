"""Container backends for the CTFd plugin PoC."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import random
from typing import Any
from typing import Iterable
from typing import Protocol
from uuid import uuid4

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from docker.tls import TLSConfig

LOGGER = logging.getLogger(__name__)


class BackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class PortBinding:
    container_port: int
    host_port: int


@dataclass(frozen=True)
class StartedContainer:
    container_id: str
    port_bindings: tuple[PortBinding, ...]


@dataclass(frozen=True)
class PublishedPortRange:
    start: int
    end: int

    def ordered_candidates(self) -> Iterable[int]:
        size = (self.end - self.start) + 1
        offset = random.randint(0, size - 1)
        for index in range(size):
            yield self.start + ((offset + index) % size)


class ContainerBackend(Protocol):
    def import_image_archive(self, *, archive_path: str, image_tag: str) -> str:
        ...

    def start(
        self,
        *,
        instance_name: str,
        image: str,
        network_name: str,
        container_ports: list[int],
        cpu_limit: float,
        memory_limit_mb: int,
        archive_path: str | None,
        labels: dict[str, str],
        published_port_range: PublishedPortRange | None,
    ) -> StartedContainer:
        ...

    def stop(self, *, container_id: str, network_name: str) -> None:
        ...

    def health(self) -> dict[str, str]:
        ...


class DockerBackend:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        tls_config: TLSConfig | bool | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        client_kwargs = {"timeout": max(int(timeout_seconds), 5)}
        if base_url:
            client_kwargs["base_url"] = base_url
            if tls_config is not None:
                client_kwargs["tls"] = tls_config
            self.client = docker.DockerClient(**client_kwargs)
            self._target = base_url
        else:
            self.client = docker.from_env(**client_kwargs)
            self._target = "environment"

    def import_image_archive(self, *, archive_path: str, image_tag: str) -> str:
        try:
            with open(archive_path, "rb") as handle:
                loaded = self.client.images.load(handle.read())
        except (OSError, APIError, DockerException) as exc:
            raise BackendError(str(exc)) from exc

        if not loaded:
            raise BackendError(f"No image found in archive '{archive_path}'")

        image = loaded[0]
        try:
            if ":" in image_tag:
                repository, tag = image_tag.rsplit(":", 1)
                image.tag(repository, tag=tag)
            else:
                image.tag(image_tag)
        except DockerException as exc:
            raise BackendError(str(exc)) from exc
        return image_tag

    def start(
        self,
        *,
        instance_name: str,
        image: str,
        network_name: str,
        container_ports: list[int],
        cpu_limit: float,
        memory_limit_mb: int,
        archive_path: str | None,
        labels: dict[str, str],
        published_port_range: PublishedPortRange | None,
    ) -> StartedContainer:
        network = None
        container = None
        try:
            try:
                self.client.images.get(image)
            except ImageNotFound:
                if archive_path:
                    self.import_image_archive(archive_path=archive_path, image_tag=image)
                else:
                    self.client.images.pull(image)

            network = self.client.networks.create(
                name=network_name,
                driver="bridge",
                check_duplicate=True,
                labels=labels,
            )
            for requested_ports in self._iter_host_ports(
                container_ports=container_ports,
                published_port_range=published_port_range,
            ):
                try:
                    container = self.client.containers.run(
                        image=image,
                        name=instance_name,
                        detach=True,
                        network=network_name,
                        ports=self._docker_port_bindings(
                            container_ports=container_ports,
                            requested_ports=requested_ports,
                        ),
                        mem_limit=f"{memory_limit_mb}m",
                        nano_cpus=max(int(cpu_limit * 1_000_000_000), 100_000_000),
                        read_only=True,
                        pids_limit=128,
                        cap_drop=["ALL"],
                        security_opt=["no-new-privileges:true"],
                        tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
                        labels=labels,
                    )
                except APIError as exc:
                    if requested_ports is not None and self._is_port_conflict(exc):
                        continue
                    raise

                container.reload()
                resolved_bindings: list[PortBinding] = []
                for container_port in container_ports:
                    port_binding = container.attrs["NetworkSettings"]["Ports"].get(
                        f"{container_port}/tcp"
                    )
                    if not port_binding:
                        raise BackendError(
                            f"Container started without a published host port for {container_port}/tcp"
                        )
                    resolved_bindings.append(
                        PortBinding(
                            container_port=container_port,
                            host_port=int(port_binding[0]["HostPort"]),
                        )
                    )
                return StartedContainer(
                    container_id=container.id,
                    port_bindings=tuple(resolved_bindings),
                )

            raise BackendError(
                "No free host ports available in the configured published port range"
            )
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
            return {"backend": "docker", "status": "ok", "target": self._target}
        except DockerException as exc:
            return {
                "backend": "docker",
                "status": f"error: {exc}",
                "target": self._target,
            }

    def _iter_host_ports(
        self,
        *,
        container_ports: list[int],
        published_port_range: PublishedPortRange | None,
    ) -> Iterable[list[int] | None]:
        if published_port_range is None:
            yield None
            return

        reserved_ports = self._list_reserved_host_ports()
        available_ports = [
            port for port in published_port_range.ordered_candidates() if port not in reserved_ports
        ]
        required_ports = len(container_ports)
        if len(available_ports) < required_ports:
            return

        last_start = len(available_ports) - required_ports
        for start_index in range(last_start + 1):
            yield available_ports[start_index : start_index + required_ports]

    def _docker_port_bindings(
        self,
        *,
        container_ports: list[int],
        requested_ports: list[int] | None,
    ) -> dict[str, tuple[str, int] | None]:
        if requested_ports is not None and len(requested_ports) != len(container_ports):
            raise BackendError("Configured host port selection does not match container ports")

        bindings: dict[str, tuple[str, int] | None] = {}
        for index, container_port in enumerate(container_ports):
            if requested_ports is None:
                bindings[f"{container_port}/tcp"] = None
            else:
                bindings[f"{container_port}/tcp"] = ("0.0.0.0", requested_ports[index])
        return bindings

    def _list_reserved_host_ports(self) -> set[int]:
        reserved_ports: set[int] = set()
        try:
            containers = self.client.containers.list(all=True)
        except DockerException:
            LOGGER.exception("failed to inspect existing containers for reserved ports")
            return reserved_ports

        for container in containers:
            try:
                container.reload()
            except DockerException:
                continue
            ports = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
            for bindings in ports.values():
                if not bindings:
                    continue
                for binding in bindings:
                    host_port = binding.get("HostPort")
                    if not host_port:
                        continue
                    try:
                        reserved_ports.add(int(host_port))
                    except (TypeError, ValueError):
                        continue
        return reserved_ports

    @staticmethod
    def _is_port_conflict(exc: APIError) -> bool:
        message = str(exc).lower()
        conflict_markers = (
            "port is already allocated",
            "address already in use",
            "bind for 0.0.0.0:",
            "failed programming external connectivity",
        )
        return any(marker in message for marker in conflict_markers)


class InMemoryBackend:
    """Small mock backend for dry-runs without Docker."""

    def __init__(self) -> None:
        self._containers: dict[str, dict[str, Any]] = {}

    def start(
        self,
        *,
        instance_name: str,
        image: str,
        network_name: str,
        container_ports: list[int],
        cpu_limit: float,
        memory_limit_mb: int,
        archive_path: str | None,
        labels: dict[str, str],
        published_port_range: PublishedPortRange | None,
    ) -> StartedContainer:
        container_id = f"mock-{uuid4().hex[:12]}"
        if published_port_range is not None:
            available_ports = list(published_port_range.ordered_candidates())
            if len(available_ports) < len(container_ports):
                raise BackendError(
                    "No free host ports available in the configured published port range"
                )
            selected_ports = available_ports[: len(container_ports)]
        else:
            selected_ports = random.sample(range(30000, 45001), len(container_ports))

        port_bindings = tuple(
            PortBinding(
                container_port=container_ports[index],
                host_port=selected_ports[index],
            )
            for index in range(len(container_ports))
        )
        self._containers[container_id] = {
            "instance_name": instance_name,
            "image": image,
            "network_name": network_name,
            "container_ports": list(container_ports),
            "cpu_limit": cpu_limit,
            "memory_limit_mb": memory_limit_mb,
            "port_bindings": [binding.__dict__ for binding in port_bindings],
            "labels": str(labels),
        }
        return StartedContainer(container_id=container_id, port_bindings=port_bindings)

    def import_image_archive(self, *, archive_path: str, image_tag: str) -> str:
        return image_tag

    def stop(self, *, container_id: str, network_name: str) -> None:
        self._containers.pop(container_id, None)

    def health(self) -> dict[str, str]:
        return {"backend": "mock", "status": "ok"}
